// SÉRIES TEMPORELLES : page dans le châssis du dashboard BI. Les calculs (moindres carrés, moyennes mobiles, additif/multiplicatif,
// estimation avec intervalle de confiance) viennent du serveur (engine/series_temporelles.py) : ici on ne fait que CHOISIR les tables et
// colonnes, LANCER l'analyse et AFFICHER le résultat (graphique SVG sans dépendance, coefficients saisonniers, estimations, export CSV, exploration
// étendue : statistiques, croissance, atypiques, cycles, profil par cycle, liens entre séries — engine/series_extras.py).
(function () {
"use strict";
const C = window.SERIES;
const TABLES = JSON.parse(document.getElementById("st-donnees-tables").textContent || "[]");
const COUL = {serie: gyCouleur("--texte-clair","#d4d4d4"), ajustement: gyCouleur("--forme-orange","#f5a524"), tendance: gyCouleur("--ok","#2ecc71"), mm: gyCouleur("--forme-violet","#b48ead"), prevision: gyCouleur("--accent","#6bcfff")};
const S = {res: null, demo: false, tables: TABLES};
const ctx = GYContexte.init({page: "series", tableId: C.tableId});

// ── Utilitaires ──────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
function el(tag, attrs, ...enfants) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) { if (k === "class") n.className = v; else if (k.startsWith("on")) n[k] = v; else n.setAttribute(k, v); }
  for (const e of enfants) if (e != null) n.append(e instanceof Node ? e : document.createTextNode(String(e)));
  return n;
}
function sv(tag, attrs, ...enfants) {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
  for (const e of enfants) if (e != null) n.append(e instanceof Node ? e : document.createTextNode(String(e)));
  return n;
}
function toast(msg, err) {
  const t = $("toast"); t.textContent = msg; t.className = err ? "err" : ""; t.style.display = "block";
  clearTimeout(t._t); t._t = setTimeout(() => { t.style.display = "none"; }, 3500);
}
async function api(url) {
  const res = await fetch(url, {headers: {"Accept": "application/json"}});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { toast(data.erreur || (tj("engine/static/engine/js/series_temporelles.js:34:41") + res.status), true); throw data; }
  return data;
}
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const nf = new Intl.NumberFormat((window.I18N_LOCALE||"fr-FR"), {maximumSignificantDigits: 5});
const fmt = v => (v == null || !isFinite(v)) ? "—" : nf.format(v);
const fmtPct = v => (v == null || !isFinite(v)) ? "—" : nf.format(v) + " %";

// ── Sélection des tables et colonnes ─────────────────────────────────────────
function dessinerTables() {
  const zone = $("st-tables");
  zone.textContent = "";
  if (!S.tables.length) { zone.append(el("div", {class: "geo-note"}, tj("engine/static/engine/js/series_temporelles.js:46:71"))); return; }
  for (const t of S.tables) {
    const courante = t.id === C.tableId;
    // Colonne date : choisie par l'utilisateur (jamais devinée) ; colonnes de valeurs : cochées une à une.
    const choixDate = el("select", {class: "st-temps", "data-temps": t.id, title: tj("engine/static/engine/js/series_temporelles.js:50:84")},
      el("option", {value: ""}, tj("engine/static/engine/js/series_temporelles.js:51:34")),
      ...(t.temps_colonnes || [{slug: "", nom: t.temps}]).map(c => el("option", {value: c.slug}, c.nom)));
    const cases = t.colonnes.map(c => el("label", {class: "geo-case st-col"},
      el("input", {type: "checkbox", "data-table": t.id, "data-slug": c.slug}), " " + c.nom,
      el("input", {type: "checkbox", "data-expl": t.id, "data-slug": c.slug, style: "margin-left:8px", title: tj("engine/static/engine/js/series_temporelles.js:55:112")}), el("small", {}, " explicative")));
    zone.append(el("div", {class: "st-table"},
      el("b", {}, t.nom + (courante ? " (ouverte)" : "")),
      el("label", {class: "geo-champ"}, tj("engine/static/engine/js/series_temporelles.js:58:42"), choixDate),
      el("div", {class: "geo-note"}, tj("engine/static/engine/js/series_temporelles.js:59:39")), ...cases));
  }
  if (window.GYFiltreTables) GYFiltreTables.installer(zone, {courant: C.tableId});   // une seule table affichée à la fois (filtre)
}
// Retourne null (avec un message) si une table a des valeurs cochées mais pas de colonne date choisie.
function selection() {
  const par = new Map();
  document.querySelectorAll("#st-tables input[data-table]:checked").forEach(i => {
    const id = Number(i.getAttribute("data-table"));
    if (!par.has(id)) par.set(id, []);
    par.get(id).push(i.getAttribute("data-slug"));
  });
  const ids = S.tables.map(t => t.id).filter(id => par.has(id));
  const temps = [];
  for (const id of ids) {
    const slug = (document.querySelector(`#st-tables select[data-temps="${id}"]`) || {}).value;
    if (!slug) { const t = S.tables.find(x => x.id === id); toast(tj("engine/static/engine/js/series_temporelles.js:74:68") + (t ? t.nom : id) + " ».", true); return null; }
    temps.push(id + ":" + slug);
  }
  const explicatives = Array.from(document.querySelectorAll("#st-tables input[data-expl]:checked")).map(i => i.getAttribute("data-expl") + ":" + i.getAttribute("data-slug"));
  return {ids, temps, colonnes: ids.flatMap(id => par.get(id).map(s => id + ":" + s)), explicatives};
}
function parametres() {
  const q = new URLSearchParams();
  for (const [id, cle] of [["p-horizon", "horizon"], ["p-periode", "periode"], ["p-confiance", "confiance"], ["p-methode", "methode"], ["p-modele", "modele"], ["p-agregat", "agregat"], ["p-glissante", "glissante"], ["p-pays", "pays"]]) {
    const v = $(id).value.trim();
    if (v) q.set(cle, v);
  }
  if (!$("p-feries").checked) q.set("feries", "0");
  const lune = Array.from(document.querySelectorAll("#st-lune input:checked")).map(i => i.value);
  if (lune.length) q.set("lunaires", lune.join(","));
  if (!$("p-ruptures").checked) q.set("ruptures", "0");
  return q;
}

// ── Analyse ──────────────────────────────────────────────────────────────────
function message(texte, avecBoutonTest) {
  const v = $("st-vide");
  v.textContent = "";
  v.style.display = texte ? "block" : "none";
  if (texte) v.append(el("div", {}, texte));
  if (texte && avecBoutonTest) v.append(el("button", {class: "principal", onclick: () => analyser(true)}, tj("engine/static/engine/js/series_temporelles.js:99:108")));
}
async function analyser(demo) {
  const q = parametres();
  let url;
  if (demo) {
    q.set("demo", "1");
    url = `/api/tables/${C.tableId}/series-temporelles/analyse/?` + q;
  } else {
    const sel = selection();
    if (!sel) return;
    if (!sel.ids.length) { toast(tj("engine/static/engine/js/series_temporelles.js:110:35"), true); return; }
    q.set("temps", sel.temps.join(","));
    q.set("tables", sel.ids.slice(1).join(","));
    q.set("colonnes", sel.colonnes.join(","));
    if (sel.explicatives.length) q.set("explicatives", sel.explicatives.join(","));
    url = `/api/tables/${sel.ids[0]}/series-temporelles/analyse/?` + q;
  }
  $("btn-analyser").disabled = $("btn-demo").disabled = true;
  message(tj("engine/static/engine/js/series_temporelles.js:118:12"));
  try {
    S.res = await api(url);
    S.demo = demo;
    ctx.noter({analyse: true, demo: !!demo});   // la page se relancera d'elle-même au retour
    message("");
    if (S.res.refusees && S.res.refusees.length) toast(S.res.refusees.length + tj("engine/static/engine/js/series_temporelles.js:124:81"), true);
    rendre();
  } catch (e) {
    message(tj("engine/static/engine/js/series_temporelles.js:127:14") + (e && e.erreur ? " " + e.erreur : ""));
  } finally {
    $("btn-analyser").disabled = $("btn-demo").disabled = false;
  }
}

// ── Graphiques SVG ───────────────────────────────────────────────────────────
function graduations(min, max, n) {
  const brut = (max - min) / n || 1, mag = Math.pow(10, Math.floor(Math.log10(brut)));
  const pas = [1, 2, 2.5, 5, 10].map(m => m * mag).find(p => p >= brut) || 10 * mag, t = [];
  for (let k = Math.ceil(min / pas); k * pas <= max; k++) t.push(k * pas);
  return t;
}
// Légende des couleurs d'un graphique : chaque couleur porte son libellé (entrees = [[couleur, libellé]]), au style de la légende existante.
function legendeCouleurs(entrees) {
  return el("div", {class: "st-legende"}, ...entrees.map(([c, l]) => el("span", {}, el("span", {class: "st-puce", style: "background:" + c}), " " + l)));
}
function graphique(res, o, largeur) {
  const n = res.y.length, h = res.prevision.valeur.length, N = n + h;
  const W = Math.max(300, largeur), H = W < 520 ? 260 : 320, m = {g: 58, d: 14, h: 12, b: 32}, L = W - m.g - m.d, Hh = H - m.h - m.b;
  const jeux = [res.y, o.ajustement ? res.ajustement : [], o.tendance ? res.tendance_serie : [], o.mm ? res.moyenne_mobile : [], res.prevision.bas, res.prevision.haut];
  let lo = Infinity, hi = -Infinity;
  for (const s of jeux) for (const v of s) if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  if (!isFinite(lo)) { lo = 0; hi = 1; }
  if (hi - lo < 1e-12) { lo -= 1; hi += 1; }
  const pad = (hi - lo) * 0.06; lo -= pad; hi += pad;
  const X = i => m.g + (N > 1 ? i / (N - 1) : 0.5) * L, Y = v => m.h + (1 - (v - lo) / (hi - lo)) * Hh;
  const points = (vals, debut) => vals.map((v, k) => v == null ? null : [X(debut + k), Y(v)]);   // courbes d'évolution : dégradé de leur couleur, du plus sombre (début) au plus clair (fin)
  const g = sv("svg", {viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": tj("engine/static/engine/js/series_temporelles.js:151:99")});
  for (const v of graduations(lo, hi, 5)) {
    g.append(sv("line", {x1: m.g, x2: W - m.d, y1: Y(v), y2: Y(v), stroke: gyCouleur("--bordure-forte","#1c1c1c")}));
    g.append(sv("text", {x: m.g - 6, y: Y(v) + 4, "text-anchor": "end", "font-size": 11, fill: gyCouleur("--texte-leger","#777")}, fmt(v)));
  }
  const etiquette = i => i < n ? res.x[i] : res.prevision.x[i - n];
  const nbX = Math.max(2, Math.floor(L / 90));
  for (let k = 0; k < nbX; k++) {
    const i = Math.round(k * (N - 1) / (nbX - 1));
    g.append(sv("text", {x: X(i), y: H - 10, "text-anchor": k === 0 ? "start" : k === nbX - 1 ? "end" : "middle", "font-size": 11, fill: gyCouleur("--texte-leger","#777")}, etiquette(i)));
  }
  const K = res._coul;
  const dernier = res.y[n - 1];
  const haut = [dernier, ...res.prevision.haut], bas = [dernier, ...res.prevision.bas], est = [dernier, ...res.prevision.valeur];
  let poly = "M" + haut.map((v, k) => X(n - 1 + k).toFixed(1) + " " + Y(v).toFixed(1)).join("L");
  poly += "L" + bas.map((v, k) => X(n - 1 + k).toFixed(1) + " " + Y(v).toFixed(1)).reverse().join("L") + "Z";
  g.append(sv("path", {d: poly, fill: K.prevision, "fill-opacity": .2, stroke: "none"}));
  g.append(sv("line", {x1: X(n - 1), x2: X(n - 1), y1: m.h, y2: m.h + Hh, stroke: gyCouleur("--bordure-forte","#444"), "stroke-dasharray": "4 4"}));
  g.append(sv("text", {x: X(n - 1) + 6, y: m.h + 11, "font-size": 11, fill: gyCouleur("--texte-leger","#777")}, "estimations"));
  if (o.mm) g.append(gyLigneDegrade(points(res.moyenne_mobile, 0), K.mm, {"stroke-width": 1.6}));
  if (o.tendance) g.append(gyLigneDegrade(points(res.tendance_serie, 0), K.tendance, {"stroke-width": 1.6, "stroke-dasharray": "2 4"}));
  if (o.ajustement) g.append(gyLigneDegrade(points(res.ajustement, 0), K.ajustement, {"stroke-width": 1.4, "stroke-dasharray": "5 3"}));
  g.append(gyLigneDegrade(points(res.y, 0), K.serie, {"stroke-width": 1.8}));
  g.append(gyLigneDegrade(points(est, n - 1), K.prevision, {"stroke-width": 2.4}));
  const repere = sv("line", {y1: m.h, y2: m.h + Hh, stroke: gyCouleur("--bordure-forte","#666"), visibility: "hidden"}), zone = sv("rect", {x: m.g, y: m.h, width: L, height: Hh, fill: "transparent"});
  g.append(repere, zone);
  const bulle = el("div", {class: "st-bulle", style: "display:none"}), boite = el("div", {class: "st-graphique"}, g, bulle);
  const survol = ev => {
    const r = g.getBoundingClientRect(), i = clamp(Math.round(((ev.clientX - r.left) * (W / r.width) - m.g) / L * (N - 1)), 0, N - 1);
    const lignes = [etiquette(i)];
    if (i < n) { lignes.push(tj("engine/static/engine/js/series_temporelles.js:180:31") + fmt(res.y[i])); if (o.ajustement) lignes.push("Ajustement : " + fmt(res.ajustement[i])); }
    else { const k = i - n; lignes.push(tj("engine/static/engine/js/series_temporelles.js:ligne_estimation", {v1: fmt(res.prevision.valeur[k])}), tj("engine/static/engine/js/series_temporelles.js:ligne_intervalle", {v1: fmt(res.prevision.bas[k]), v2: fmt(res.prevision.haut[k])})); }
    bulle.textContent = ""; lignes.forEach(t => bulle.append(el("div", {}, t)));
    bulle.style.display = "block";
    bulle.style.left = (X(i) / W * r.width + (X(i) > W * 0.6 ? -bulle.offsetWidth - 8 : 8)) + "px";
    repere.setAttribute("x1", X(i)); repere.setAttribute("x2", X(i)); repere.setAttribute("visibility", "visible");
  };
  zone.addEventListener("pointermove", survol);
  zone.addEventListener("pointerdown", survol);
  zone.addEventListener("pointerleave", () => { bulle.style.display = "none"; repere.setAttribute("visibility", "hidden"); });
  return boite;
}
function graphiqueSaison(saison, largeur) {
  const p = saison.indices.length, W = Math.max(300, largeur), H = 130, m = {g: 58, d: 14, h: 10, b: 26};
  const base = saison.modele === "multiplicatif" ? 1 : 0, pk = gyPaletteSuite(p);
  const ext = Math.max(...saison.indices.map(v => Math.abs(v - base)), 1e-9) * 1.15;
  const Y = v => m.h + (1 - (v - (base - ext)) / (2 * ext)) * (H - m.h - m.b), bw = (W - m.g - m.d) / p;
  const g = sv("svg", {viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": tj("engine/static/engine/js/series_temporelles.js:lbl_coefficients_saisonniers")});
  g.append(sv("text", {x: m.g - 6, y: Y(base) + 4, "text-anchor": "end", "font-size": 11, fill: gyCouleur("--texte-leger","#777")}, String(base)));
  saison.indices.forEach((v, k) => {
    const y0 = Y(base), y1 = Y(v);
    g.append(sv("rect", {x: m.g + k * bw + bw * 0.15, y: Math.min(y0, y1), width: bw * 0.7, height: Math.max(1, Math.abs(y1 - y0)), fill: pk[k]},
      sv("title", {}, saison.phases[k] + " : " + fmt(v))));
    if (k % Math.ceil(p / 12) === 0) g.append(sv("text", {x: m.g + k * bw + bw / 2, y: H - 8, "text-anchor": "middle", "font-size": 10, fill: gyCouleur("--texte-leger","#777")}, saison.phases[k]));
  });
  g.append(sv("line", {x1: m.g, x2: W - m.d, y1: Y(base), y2: Y(base), stroke: gyCouleur("--bordure-forte","#555")}));
  return el("div", {}, el("div", {class: "st-graphique"}, g), legendeCouleurs(saison.phases.map((ph, k) => [pk[k], String(ph)])));
}

function barres(valeurs, seuil, largeur, titre, etiquettes) {
  const p = valeurs.length, W = Math.max(300, largeur), H = 110, m = {g: 58, d: 14, h: 10, b: 22}, ext = Math.max(1, ...valeurs.map(Math.abs)) * 1.05;
  const Y = v => m.h + (1 - (v + ext) / (2 * ext)) * (H - m.h - m.b), bw = (W - m.g - m.d) / p, pb = gyPaletteSuite(p + 1);
  const g = sv("svg", {viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": titre});
  g.append(sv("rect", {x: m.g, y: Y(seuil), width: W - m.g - m.d, height: Y(-seuil) - Y(seuil), fill: pb[p], "fill-opacity": .18}),
    sv("line", {x1: m.g, x2: W - m.d, y1: Y(0), y2: Y(0), stroke: gyCouleur("--bordure-forte","#555")}), sv("text", {x: m.g - 6, y: Y(0) + 4, "text-anchor": "end", "font-size": 11, fill: gyCouleur("--texte-leger","#777")}, "0"));
  valeurs.forEach((v, k) => {
    const dehors = Math.abs(v) > seuil;
    g.append(sv("rect", {x: m.g + k * bw + bw * 0.2, y: Math.min(Y(0), Y(v)), width: bw * 0.6, height: Math.max(1, Math.abs(Y(v) - Y(0))), fill: pb[k]}, sv("title", {}, "retard " + (k + 1) + " : " + fmt(v))));
    if (k % Math.ceil(p / 12) === 0) g.append(sv("text", {x: m.g + k * bw + bw / 2, y: H - 6, "text-anchor": "middle", "font-size": 10, fill: gyCouleur("--texte-leger","#777")}, String(k + 1)));
  });
  return el("div", {}, el("div", {class: "st-graphique"}, g), legendeCouleurs(valeurs.map((v, k) => [pb[k], "Retard " + (k + 1)]).concat([[pb[p], "Zone sans autocorrélation significative"]])));
}
function courbeResidus(res, largeur) {
  const r = res.diagnostic.residus, n = r.length, W = Math.max(300, largeur), H = 120, m = {g: 58, d: 14, h: 10, b: 10}, s = res.qualite.sigma || 1;
  const ext = Math.max(2 * s, ...r.map(v => v == null ? 0 : Math.abs(v))) * 1.1, X = i => m.g + (n > 1 ? i / (n - 1) : 0.5) * (W - m.g - m.d), Y = v => m.h + (1 - (v + ext) / (2 * ext)) * (H - m.h - m.b);
  const g = sv("svg", {viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": tj("engine/static/engine/js/series_temporelles.js:225:99")});
  const pc = gyPaletteSuite(2 + res.diagnostic.aberrantes.length);
  g.append(sv("rect", {x: m.g, y: Y(2 * s), width: W - m.g - m.d, height: Y(-2 * s) - Y(2 * s), fill: pc[0], "fill-opacity": .16}), sv("line", {x1: m.g, x2: W - m.d, y1: Y(0), y2: Y(0), stroke: gyCouleur("--bordure-forte","#555")}),
    gyLigneDegrade(r.map((v, i) => v == null ? null : [X(i), Y(v)]), pc[1], {"stroke-width": 1.4}));
  res.diagnostic.aberrantes.forEach((a, k) => { const i = res.x.indexOf(a.x); if (i >= 0) g.append(sv("circle", {cx: X(i), cy: Y(a.ecart), r: 4, fill: pc[2 + k]}, sv("title", {}, a.x + " : " + fmt(a.ecart)))); });
  return el("div", {}, el("div", {class: "st-graphique"}, g), legendeCouleurs([[pc[0], "Bande d'écart normal (±2 écarts-types)"], [pc[1], "Écart entre le modèle et les données"]].concat(res.diagnostic.aberrantes.map((a, k) => [pc[2 + k], "Valeur à vérifier : " + a.x]))));
}
function courbesVariation(res, largeur) {
  const v = res.variations, N = v.periode.length, n = v.separation, W = Math.max(300, largeur), H = 170, m = {g: 58, d: 14, h: 10, b: 24};
  const pj = gyPaletteSuite(3);
  const jeux = [["periode", tj("engine/static/engine/js/series_temporelles.js:233:30"), pj[0]], ["glissement", tj("engine/static/engine/js/series_temporelles.js:233:102"), pj[1]],
    ["cvs_periode", tj("engine/static/engine/js/series_temporelles.js:234:22"), pj[2]]].filter(j => v[j[0]]);
  const vals = jeux.flatMap(j => v[j[0]]).filter(x => x != null);
  let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
  const X = i => m.g + i / Math.max(1, N - 1) * (W - m.g - m.d), Y = x => m.h + (1 - (x - lo) / (hi - lo)) * (H - m.h - m.b);
  const g = sv("svg", {viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": tj("engine/static/engine/js/series_temporelles.js:239:99")});
  for (const t of graduations(lo, hi, 4)) g.append(sv("line", {x1: m.g, x2: W - m.d, y1: Y(t), y2: Y(t), stroke: t === 0 ? gyCouleur("--texte-leger","#555") : gyCouleur("--fond-releve","#1c1c1c")}),
    sv("text", {x: m.g - 6, y: Y(t) + 4, "text-anchor": "end", "font-size": 11, fill: gyCouleur("--texte-leger","#777")}, fmt(t) + " %"));
  g.append(sv("line", {x1: X(n - 1), x2: X(n - 1), y1: m.h, y2: H - m.b, stroke: gyCouleur("--bordure-forte","#444"), "stroke-dasharray": "4 4"}));
  [0, (N - 1) >> 1, N - 1].forEach((i, k) => g.append(sv("text", {x: X(i), y: H - 8, "text-anchor": k === 0 ? "start" : k === 2 ? "end" : "middle", "font-size": 11, fill: gyCouleur("--texte-leger","#777")}, i < n ? res.x[i] : res.prevision.x[i - n])));
  const trace = (s, a, b) => { const pts = []; for (let i = a; i < b; i++) pts.push(s[i] == null ? null : [X(i), Y(s[i])]); return pts; };
  for (const [cle, , couleur] of jeux) g.append(gyLigneDegrade(trace(v[cle], 0, n), couleur, {"stroke-width": 1.6}),
    gyLigneDegrade(trace(v[cle], n - 1, N), couleur, {"stroke-width": 1.6, "stroke-dasharray": "4 3"}));
  return el("div", {}, el("div", {class: "st-legende"}, ...jeux.map(j => el("span", {}, el("span", {class: "st-puce", style: "background:" + gyFondDegrade(j[2])}), " " + j[1]))), el("div", {class: "st-graphique"}, g));
}
function blocEffets(res) {
  const nature = {rupture: tj("engine/static/engine/js/series_temporelles.js:250:29"), explicative: tj("engine/static/engine/js/series_temporelles.js:nature_explicative"), calendrier: tj("engine/static/engine/js/series_temporelles.js:nature_calendrier"), saisonnalite: tj("engine/static/engine/js/series_temporelles.js:nature_saisonnalite")};
  return tableau([tj("engine/static/engine/js/series_temporelles.js:lbl_effet"), tj("engine/static/engine/js/series_temporelles.js:lbl_nature"), tj("engine/static/engine/js/series_temporelles.js:lbl_poids"), "Retenu"], res.regresseurs.map(r => ({classe: r.retenu ? "st-retenu" : "", cellules: [r.nom, nature[r.type] || r.type,
    r.retenu ? (r.relatif ? (r.coef != null ? fmtPct(r.coef * 100) + tj("engine/static/engine/js/series_temporelles.js:252:71") : "amplitude " + fmtPct(r.amplitude * 100))
      : r.coef != null ? fmt(r.coef) + tj("engine/static/engine/js/series_temporelles.js:253:41") : "amplitude " + fmt(r.amplitude)) : "—", r.retenu ? "oui" : tj("engine/static/engine/js/series_temporelles.js:253:115") + fmt(r.p) + ")"]})));
}
function blocGlissante(res) {
  const g = res.glissante;
  return el("div", {}, el("p", {class: "st-resume"}, tj("engine/static/engine/js/series_temporelles.js:257:55", {v1: g.plis, v2: g.horizon, v3: fmt(g.rmse), v4: fmt(g.mae), v5: g.mape == null ? "" : ", " + fmtPct(g.mape)}) +
    tj("engine/static/engine/js/series_temporelles.js:258:6", {v1: Math.round(g.couverture * 100), v2: fmt(g.rmse_naif)}) +
    (g.gain_naif == null ? "." : tj(g.gain_naif >= 0 ? "engine/static/engine/js/series_temporelles.js:lbl_gain" : "engine/static/engine/js/series_temporelles.js:lbl_perte", {v1: fmt(Math.abs(g.gain_naif) * 100)}))),
    tableau([tj("engine/static/engine/js/series_temporelles.js:260:15"), tj("engine/static/engine/js/series_temporelles.js:260:37"), "RMSE", "MAE"], g.origines.map(o => ({cellules: [o.apres, o.methode, fmt(o.rmse), fmt(o.mae)]}))),
    ...(g.par_methode && g.par_methode.length > 1 ? [el("div", {class: "st-section"}, tj("engine/static/engine/js/series_temporelles.js:261:88")),
      tableau([tj("engine/static/engine/js/series_temporelles.js:262:17"), "RMSE", "MAE", tj("engine/static/engine/js/series_temporelles.js:lbl_intervalle_juste"), tj("engine/static/engine/js/series_temporelles.js:262:63")], g.par_methode.map(m => ({cellules: [m.methode_libelle, fmt(m.rmse), fmt(m.mae),
        Math.round(m.couverture * 100) + " %", m.gain_naif == null ? "—" : fmtPct(m.gain_naif * 100)]}))),
      ...(g.incomplet ? [el("p", {class: "st-resume"}, tj("engine/static/engine/js/series_temporelles.js:264:57"))] : [])] : []),
    el("details", {}, el("summary", {}, tj("engine/static/engine/js/series_temporelles.js:265:42")), tableau([tj("engine/static/engine/js/series_temporelles.js:265:82"), "RMSE"], g.par_pas.map((x, k) => ({cellules: [k + 1, fmt(x)]})))));
}
function blocDiagnostic(res, largeur) {
  const d = res.diagnostic, f = d.fiabilite, niveau = {bonne: tj("engine/static/engine/js/series_temporelles.js:268:64"), moyenne: tj("engine/static/engine/js/series_temporelles.js:268:92"), faible: tj("engine/static/engine/js/series_temporelles.js:268:121")}[f.niveau];
  return el("div", {}, el("div", {class: "st-fiab st-fiab-" + f.niveau}, el("b", {}, niveau), ...f.phrases.map(t => el("div", {}, t))),
    el("div", {class: "st-section"}, tj("engine/static/engine/js/series_temporelles.js:270:39")), courbeResidus(res, largeur),
    el("details", {}, el("summary", {}, tj("engine/static/engine/js/series_temporelles.js:271:42")),
      el("div", {class: "st-section"}, tj("engine/static/engine/js/series_temporelles.js:272:41")), barres(d.autocorrelation.acf, d.autocorrelation.seuil, largeur, tj("engine/static/engine/js/series_temporelles.js:272:188")),
      tableau([tj("engine/static/engine/js/series_temporelles.js:273:17"), tj("engine/static/engine/js/series_temporelles.js:273:29")], [
        {cellules: [tj("engine/static/engine/js/series_temporelles.js:274:22"), (d.blancheur.ok ? "oui" : "non") + " — p = " + fmt(d.blancheur.p)]},
        {cellules: [tj("engine/static/engine/js/series_temporelles.js:275:22"), (d.normalite.ok ? "oui" : "non") + " — p = " + fmt(d.normalite.p)]},
        {cellules: [tj("engine/static/engine/js/series_temporelles.js:276:22"), d.stationnarite ? (d.stationnarite.stationnaire ? "oui" : "non") + " — statistique " + fmt(d.stationnarite.statistique) + tj("engine/static/engine/js/series_temporelles.js:276:176") + fmt(d.stationnarite.seuils["5%"]) + ")" : tj("engine/static/engine/js/series_temporelles.js:276:239")]},
        {cellules: [tj("engine/static/engine/js/series_temporelles.js:277:22"), d.aberrantes.length ? d.aberrantes.map(a => a.x).join(", ") : "aucune"]}])));
}

// ── Exploration étendue (engine/series_extras.py) ────────────────────────────
function courbe(valeurs, etiquettes, largeur, titre, couleur, unite, repere) {
  const N = valeurs.length, W = Math.max(300, largeur), H = 130, m = {g: 58, d: 14, h: 10, b: 24}, vs = valeurs.filter(v => v != null);
  if (vs.length < 2) return el("div", {class: "geo-note"}, tj("engine/static/engine/js/series_temporelles.js:283:61"));
  let lo = Math.min(...vs, repere == null ? Infinity : repere), hi = Math.max(...vs, repere == null ? -Infinity : repere);
  if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
  const X = i => m.g + i / Math.max(1, N - 1) * (W - m.g - m.d), Y = v => m.h + (1 - (v - lo) / (hi - lo)) * (H - m.h - m.b);
  const g = sv("svg", {viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": titre});
  for (const t of graduations(lo, hi, 3)) g.append(sv("line", {x1: m.g, x2: W - m.d, y1: Y(t), y2: Y(t), stroke: t === repere ? gyCouleur("--texte-leger","#555") : gyCouleur("--fond-releve","#1c1c1c")}),
    sv("text", {x: m.g - 6, y: Y(t) + 4, "text-anchor": "end", "font-size": 11, fill: gyCouleur("--texte-leger","#777")}, fmt(t) + (unite || "")));
  g.append(gyLigneDegrade(valeurs.map((v, i) => v == null ? null : [X(i), Y(v)]), couleur, {"stroke-width": 1.6}));
  [0, (N - 1) >> 1, N - 1].forEach((i, k) => g.append(sv("text", {x: X(i), y: H - 8, "text-anchor": k === 0 ? "start" : k === 2 ? "end" : "middle", "font-size": 11, fill: gyCouleur("--texte-leger","#777")}, etiquettes[i])));
  return el("div", {}, el("div", {class: "st-graphique"}, g), legendeCouleurs([[couleur, titre]]));
}
function histogramme(h, largeur) {
  const k = h.comptes.length, W = Math.max(300, largeur), H = 120, m = {g: 58, d: 14, h: 10, b: 22}, max = Math.max(...h.comptes, 1), bw = (W - m.g - m.d) / k, ph = gyPaletteSuite(k);
  const g = sv("svg", {viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": tj("engine/static/engine/js/series_temporelles.js:298:99")});
  g.append(sv("text", {x: m.g - 6, y: m.h + 8, "text-anchor": "end", "font-size": 11, fill: gyCouleur("--texte-leger","#777")}, String(max)));
  h.comptes.forEach((c, i) => {
    const hauteur = c / max * (H - m.h - m.b);
    g.append(sv("rect", {x: m.g + i * bw + 1, y: H - m.b - hauteur, width: Math.max(1, bw - 2), height: hauteur, fill: ph[i]},
      sv("title", {}, tj("engine/static/engine/js/series_temporelles.js:borne_a_borne_n", {v1: fmt(h.bornes[i]), v2: fmt(h.bornes[i + 1]), v3: c}))));
  });
  [0, k >> 1, k].forEach((i, j) => g.append(sv("text", {x: m.g + i * bw, y: H - 6, "text-anchor": j === 0 ? "start" : j === 2 ? "end" : "middle", "font-size": 10, fill: gyCouleur("--texte-leger","#777")}, fmt(h.bornes[i]))));
  return el("div", {}, el("div", {class: "st-graphique"}, g), legendeCouleurs(h.comptes.map((c, i) => [ph[i], fmt(h.bornes[i]) + " – " + fmt(h.bornes[i + 1])])));
}
function chaleur(pr) {   // une ligne par cycle, une colonne par phase ; couleur = écart à la moyenne de la ligne (vert au-dessus, rouge en dessous)
  return tableau(["Cycle", ...pr.phases], [...pr.lignes.map(l => {
    const vs = l.valeurs.filter(v => v != null), moy = vs.reduce((a, b) => a + b, 0) / (vs.length || 1), ext = Math.max(...vs.map(v => Math.abs(v - moy)), 1e-9);
    return {cellules: [l.x, ...l.valeurs.map(v => v == null ? "" : el("span", {style: `display:block;padding:0 4px;background:${v >= moy ? gyCouleur("--ok", "#2ecc71", (Math.abs(v - moy) / ext * .5).toFixed(2)) : gyCouleur("--danger", "#e74c3c", (Math.abs(v - moy) / ext * .5).toFixed(2))}`}, fmt(v)))]};
  }), {classe: "st-retenu", cellules: [tj("engine/static/engine/js/series_temporelles.js:312:41"), ...pr.moyenne.map(fmt)]}], "st-chaleur");
}
function blocExploration(res, largeur) {
  const e = res.exploration, s = e.statistiques, c = e.croissance;
  const forme = s.asymetrie == null ? "—" : fmt(s.asymetrie) + (Math.abs(s.asymetrie) < 0.5 ? tj("engine/static/engine/js/series_temporelles.js:316:96") : s.asymetrie > 0 ? tj("engine/static/engine/js/series_temporelles.js:316:155") : tj("engine/static/engine/js/series_temporelles.js:316:222"));
  const apres = (v, x) => fmt(v) + " (" + x + ")";
  const sections = [
    el("div", {class: "st-section"}, tj("engine/static/engine/js/series_temporelles.js:lbl_statistiques_descriptives")),
    tableau([tj("engine/static/engine/js/series_temporelles.js:lbl_indicateur"), tj("engine/static/engine/js/series_temporelles.js:320:29")], [
      {cellules: [tj("engine/static/engine/js/series_temporelles.js:321:20"), fmt(s.moyenne) + " / " + fmt(s.mediane)]},
      {cellules: [tj("engine/static/engine/js/series_temporelles.js:322:20"), fmt(s.ecart_type) + (s.cv == null ? "" : " — " + fmtPct(s.cv) + tj("engine/static/engine/js/series_temporelles.js:322:111"))]},
      {cellules: [tj("engine/static/engine/js/series_temporelles.js:323:20"), apres(s.min, s.x_min)]}, {cellules: [tj("engine/static/engine/js/series_temporelles.js:323:79"), apres(s.max, s.x_max)]},
      {cellules: [tj("engine/static/engine/js/series_temporelles.js:324:20"), fmt(s.etendue)]},
      {cellules: [tj("engine/static/engine/js/series_temporelles.js:325:20"), fmt(s.q25) + tj("engine/static/engine/js/series_temporelles.js:lbl_a") + fmt(s.q75) + tj("engine/static/engine/js/series_temporelles.js:325:85") + fmt(s.iqr) + ")"]},
      {cellules: [tj("engine/static/engine/js/series_temporelles.js:326:20"), fmt(s.q05) + " et " + fmt(s.q95)]},
      {cellules: [tj("engine/static/engine/js/series_temporelles.js:327:20"), forme]},
      {cellules: [tj("engine/static/engine/js/series_temporelles.js:lbl_concentration"), s.aplatissement == null ? "—" : fmt(s.aplatissement) + (s.aplatissement > 1 ? tj("engine/static/engine/js/series_temporelles.js:328:131") : s.aplatissement < -1 ? tj("engine/static/engine/js/series_temporelles.js:328:189") : tj("engine/static/engine/js/series_temporelles.js:328:219"))]},
      {cellules: [tj("engine/static/engine/js/series_temporelles.js:329:20") + s.n + " valeurs", fmt(s.somme)]}]),
    el("div", {class: "st-section"}, tj("engine/static/engine/js/series_temporelles.js:lbl_croissance")),
    tableau([tj("engine/static/engine/js/series_temporelles.js:lbl_indicateur"), tj("engine/static/engine/js/series_temporelles.js:331:29")], [
      {cellules: [tj("engine/static/engine/js/series_temporelles.js:332:20"), fmtPct(c.variation_totale)]},
      {cellules: [tj("engine/static/engine/js/series_temporelles.js:333:20"), c.tcac == null ? tj("engine/static/engine/js/series_temporelles.js:333:73") : fmtPct(c.tcac)]},
      {cellules: [tj("engine/static/engine/js/series_temporelles.js:334:20"), c.hausses + " / " + c.baisses + " / " + c.stables]},
      {cellules: [tj("engine/static/engine/js/series_temporelles.js:335:20"), c.serie_hausses + " / " + c.serie_baisses + tj("engine/static/engine/js/series_temporelles.js:336:124")]},
      {cellules: [tj("engine/static/engine/js/series_temporelles.js:lbl_meilleure_variation"), c.meilleure ? fmtPct(c.meilleure.pct) + " (" + c.meilleure.x + ")" : "—"]},
      {cellules: [tj("engine/static/engine/js/series_temporelles.js:lbl_pire_variation"), c.pire ? fmtPct(c.pire.pct) + " (" + c.pire.x + ")" : "—"]}]),
    el("div", {class: "st-section"}, tj("engine/static/engine/js/series_temporelles.js:338:39")),
    e.atypiques.length ? tableau([tj("engine/static/engine/js/series_temporelles.js:339:36"), tj("engine/static/engine/js/series_temporelles.js:339:47"), tj("engine/static/engine/js/series_temporelles.js:339:57"), tj("engine/static/engine/js/series_temporelles.js:339:76")], e.atypiques.map(a => ({cellules: [a.x, fmt(a.valeur), fmt(a.attendu), fmt(a.z)]})))
      : el("div", {class: "geo-note"}, tj("engine/static/engine/js/series_temporelles.js:340:41")),
    ...(e.retournements.length ? [el("div", {class: "st-section"}, tj("engine/static/engine/js/series_temporelles.js:341:69")),
      tableau([tj("engine/static/engine/js/series_temporelles.js:342:17"), tj("engine/static/engine/js/series_temporelles.js:lbl_nature"), tj("engine/static/engine/js/series_temporelles.js:342:38")], e.retournements.map(r => ({cellules: [r.x, r.type, fmt(r.valeur)]})))] : []),
    el("details", {}, el("summary", {}, tj("engine/static/engine/js/series_temporelles.js:343:42")), histogramme(e.histogramme, largeur)),
    ...(e.cycles.length ? [el("details", {}, el("summary", {}, tj("engine/static/engine/js/series_temporelles.js:lbl_cycles_dominants")), el("p", {class: "geo-note"}, tj("engine/static/engine/js/series_temporelles.js:344:115")),
      tableau([tj("engine/static/engine/js/series_temporelles.js:345:17"), tj("engine/static/engine/js/series_temporelles.js:345:29")], e.cycles.map(k => ({cellules: [fmt(k.periode) + tj("engine/static/engine/js/series_temporelles.js:346:186"), fmtPct(k.part)]}))))] : []),
    ...(e.autocorrelation ? [el("details", {}, el("summary", {}, tj("engine/static/engine/js/series_temporelles.js:346:67")), el("p", {class: "geo-note"}, tj("engine/static/engine/js/series_temporelles.js:346:120")),
      barres(e.autocorrelation.acf, e.autocorrelation.seuil, largeur, tj("engine/static/engine/js/series_temporelles.js:347:72")))] : []),
    el("details", {}, el("summary", {}, tj("engine/static/engine/js/series_temporelles.js:348:42") + e.volatilite.fenetre + tj("engine/static/engine/js/series_temporelles.js:349:126")), courbe(e.volatilite.valeurs, res.x, largeur, tj("engine/static/engine/js/series_temporelles.js:348:182"), gyPaletteSuite(1)[0])),
    ...(e.base100 ? [el("details", {}, el("summary", {}, tj("engine/static/engine/js/series_temporelles.js:349:59")), courbe(e.base100, res.x, largeur, tj("engine/static/engine/js/series_temporelles.js:lbl_indice_base_100"), gyPaletteSuite(1)[0], "", 100))] : []),
    ...(e.profil ? [el("details", {}, el("summary", {}, tj("engine/static/engine/js/series_temporelles.js:350:58")), chaleur(e.profil))] : [])];
  return el("div", {}, ...sections);
}
function blocComparaison(c) {
  const phrase = p => p.decalage == null ? "—" : p.decalage === 0 ? tj("engine/static/engine/js/series_temporelles.js:354:70") : p.decalage > 0 ? "« " + p.a + tj("engine/static/engine/js/series_temporelles.js:354:119") + p.b + tj("engine/static/engine/js/series_temporelles.js:guillemet_de") + p.decalage + tj("engine/static/engine/js/series_temporelles.js:355:253") : "« " + p.b + tj("engine/static/engine/js/series_temporelles.js:354:196") + p.a + tj("engine/static/engine/js/series_temporelles.js:guillemet_de") + (-p.decalage) + tj("engine/static/engine/js/series_temporelles.js:355:377");
  const teinte = v => v == null ? "" : `background:${v >= 0 ? gyCouleur("--ok", "#2ecc71", (Math.abs(v) * .6).toFixed(2)) : gyCouleur("--danger", "#e74c3c", (Math.abs(v) * .6).toFixed(2))}`;
  return el("div", {class: "st-carte"}, el("div", {class: "st-titre"}, el("b", {}, tj("engine/static/engine/js/series_temporelles.js:356:85"))),
    el("p", {class: "geo-note"}, tj("engine/static/engine/js/series_temporelles.js:357:35")),
    el("div", {class: "st-defile"}, el("table", {class: tj("engine/static/engine/js/series_temporelles.js:358:58")}, el("thead", {}, el("tr", {}, el("th", {}, ""), ...c.noms.map(n => el("th", {}, n)))),
      el("tbody", {}, ...c.noms.map((n, i) => el("tr", {}, el("td", {}, el("b", {}, n)), ...c.matrice[i].map(v => el("td", {style: teinte(v)}, v == null ? "—" : fmt(v)))))))),
    el("div", {class: "st-section"}, tj("engine/static/engine/js/series_temporelles.js:360:39")),
    tableau([tj("engine/static/engine/js/series_temporelles.js:361:15"), tj("engine/static/engine/js/series_temporelles.js:361:26"), tj("engine/static/engine/js/series_temporelles.js:lbl_variations"), tj("engine/static/engine/js/series_temporelles.js:lbl_niveaux"), tj("engine/static/engine/js/series_temporelles.js:361:62"), tj("engine/static/engine/js/series_temporelles.js:lbl_lien_net")], c.paires.map(p => ({cellules: [p.a, p.b, fmt(p.correlation_variations), fmt(p.correlation_niveaux),
      phrase(p) + (p.decalage ? tj("engine/static/engine/js/series_temporelles.js:363:34") + fmt(p.correlation_decalee) + ")" : ""), p.significatif ? tj("engine/static/engine/js/series_temporelles.js:oui") : tj("engine/static/engine/js/series_temporelles.js:non")]}))));
}

// ── Cartes de résultat ───────────────────────────────────────────────────────
function tableau(entetes, lignes, classe) {
  return el("div", {class: "st-defile"}, el("table", {class: "st-tableau " + (classe || "")},
    el("thead", {}, el("tr", {}, ...entetes.map(t => el("th", {}, t)))),
    el("tbody", {}, ...lignes.map(l => el("tr", {class: l.classe || ""}, ...l.cellules.map(c => el("td", {}, c)))))));
}
function exporter(res) {
  const p = res.prevision, virg = v => v == null ? "" : String(v).replace(".", ",");
  const csv = tj("engine/static/engine/js/series_temporelles.js:373:16") + p.x.map((x, k) => [x, virg(p.valeur[k]), virg(p.bas[k]), virg(p.haut[k])].join(";")).join("\n") + "\n";
  const a = el("a", {href: URL.createObjectURL(new Blob([csv], {type: "text/csv;charset=utf-8"})), download: (res.table + "_" + res.colonne + "_estimations.csv").replace(/[^\w.-]+/g, "_")});
  document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
function carte(res, avecTable) {
  const titre = el("div", {class: "st-titre"}, el("b", {}, res.colonne || res.nom), avecTable ? el("span", {class: "st-sous"}, " — " + res.table) : null);
  if (res.erreur) return el("div", {class: "st-carte"}, titre, el("div", {class: "geo-note"}, res.erreur));
  const o = res._o || (res._o = {tendance: true, mm: false, ajustement: true});
  const pg = gyPaletteSuite(5);   // une couleur hexadécimale par élément du graphique principal : série, ajustement, tendance, moyenne mobile, estimations
  const K = res._coul = {serie: pg[0], ajustement: pg[1], tendance: pg[2], mm: pg[3], prevision: pg[4]};
  const mr = res.modele_retenu, q = res.qualite, v = q.validation;
  const badges = el("div", {class: "st-badges"},
    ...[res.frequence.libelle, res.saisonnalite.detectee ? tj("engine/static/engine/js/series_temporelles.js:saisonnalite_periode") + res.periode : tj("engine/static/engine/js/series_temporelles.js:383:93"), mr.methode_libelle,
      mr.modele === "aucun" ? null : tj("engine/static/engine/js/series_temporelles.js:385:39") + mr.modele, mr.degre == null ? null : mr.degre === 2 ? tj("engine/static/engine/js/series_temporelles.js:lbl_tendance_parabolique") : tj("engine/static/engine/js/series_temporelles.js:lbl_tendance_droite"), res.tendance.sens]
      .filter(Boolean).map(t => el("span", {class: "st-badge"}, t)));
  const boite = el("div", {}), redessiner = () => { boite.textContent = ""; boite.append(graphique(res, o, boite.parentElement ? boite.parentElement.clientWidth - 2 : 640)); };
  const bascule = (cle, nom, couleur) => el("label", {class: "geo-case st-bascule"},
    el("input", {type: "checkbox", ...(o[cle] ? {checked: "checked"} : {}), onchange: e => { o[cle] = e.target.checked; redessiner(); }}),
    el("span", {class: "st-puce", style: "background:" + gyFondDegrade(couleur)}), " " + nom);
  const legende = el("div", {class: "st-legende"},
    el("span", {}, el("span", {class: "st-puce", style: "background:" + gyFondDegrade(K.serie)}), tj("engine/static/engine/js/series_temporelles.js:391:88")),
    bascule("ajustement", tj("engine/static/engine/js/series_temporelles.js:lbl_ajustement"), K.ajustement), bascule("tendance", tj("engine/static/engine/js/series_temporelles.js:lbl_tendance"), K.tendance),
    bascule("mm", tj("engine/static/engine/js/series_temporelles.js:393:20") + res.ordre_moyenne_mobile + ")", K.mm),
    el("span", {}, el("span", {class: "st-puce", style: "background:" + gyFondDegrade(K.prevision)}), tj("engine/static/engine/js/series_temporelles.js:394:92") + Math.round(res.prevision.confiance * 100) + " %"));
  const stats = tableau([tj("engine/static/engine/js/series_temporelles.js:lbl_indicateur"), tj("engine/static/engine/js/series_temporelles.js:395:41")], [
    {cellules: [res.tendance.propre_au_modele ? tj("engine/static/engine/js/series_temporelles.js:396:50") : tj("engine/static/engine/js/series_temporelles.js:lbl_tendance"), res.tendance.equation + tj("engine/static/engine/js/series_temporelles.js:396:125")]},
    {cellules: [tj("engine/static/engine/js/series_temporelles.js:397:18"), fmt(res.tendance.pente_par_periode) + " " + res.tendance.unite]},
    {cellules: [tj("engine/static/engine/js/series_temporelles.js:398:18"), fmt(q.rmse) + " / " + fmt(q.mae)]},
    {cellules: [tj("engine/static/engine/js/series_temporelles.js:399:18"), fmtPct(q.mape)]},
    {cellules: [tj("engine/static/engine/js/series_temporelles.js:400:18"), q.r2 == null ? "—" : fmt(q.r2 * 100) + " %"]},
    {cellules: [tj("engine/static/engine/js/series_temporelles.js:401:18") + (v ? v.k : 0) + tj("engine/static/engine/js/series_temporelles.js:401:58"), v ? "RMSE " + fmt(v.rmse) + " ; intervalle juste " + Math.round(v.couverture * 100) + tj("engine/static/engine/js/series_temporelles.js:401:166") : tj("engine/static/engine/js/series_temporelles.js:401:182")]},
    {cellules: [tj("engine/static/engine/js/series_temporelles.js:402:18"), res.n + tj("engine/static/engine/js/series_temporelles.js:402:37") + res.donnees.interpoles + tj("engine/static/engine/js/series_temporelles.js:403:172") + res.donnees.ignores + tj("engine/static/engine/js/series_temporelles.js:403:212")]}]);
  const prev = res.prevision;
  const tabPrev = tableau([tj("engine/static/engine/js/series_temporelles.js:404:29"), tj("engine/static/engine/js/series_temporelles.js:lbl_estimation"), "Bas", "Haut"], prev.x.map((x, k) => ({cellules: [x, fmt(prev.valeur[k]), fmt(prev.bas[k]), fmt(prev.haut[k])]})));
  const tabCand = tableau([tj("engine/static/engine/js/series_temporelles.js:405:29"), tj("engine/static/engine/js/series_temporelles.js:405:40"), tj("engine/static/engine/js/series_temporelles.js:lbl_tendance"), tj("engine/static/engine/js/series_temporelles.js:405:62"), tj("engine/static/engine/js/series_temporelles.js:405:86")], res.candidats.map(c => ({
    classe: c.retenu ? "st-retenu" : "", cellules: [c.methode_libelle, c.modele, c.degre == null ? "—" : c.degre === 2 ? "parabole" : "droite", fmt(c.rmse_validation), fmt(c.rmse_ajustement)]})));
  const c = el("div", {class: "st-carte"}, titre, badges, el("p", {class: "st-resume"}, res.resume), el("p", {class: "geo-note"}, "Choix : " + mr.raison),
    ...(res.avertissements || []).map(a => el("div", {class: "st-avert"}, a)), legende, boite,
    res.saison ? el("div", {class: "st-section"}, tj("engine/static/engine/js/series_temporelles.js:coeff_saisonniers_ouvrant") + (res.saison.modele === "multiplicatif" ? tj("engine/static/engine/js/series_temporelles.js:lbl_coefficient_multiplicateur") : tj("engine/static/engine/js/series_temporelles.js:409:167")) + (res.saison.propre_au_modele ? tj("engine/static/engine/js/series_temporelles.js:409:228") : "") + ")") : null,
    res.saison ? graphiqueSaison(res.saison, 640) : null,
    el("div", {class: "st-section"}, tj("engine/static/engine/js/series_temporelles.js:411:39")), courbesVariation(res, 640),
    ...(res.regresseurs.some(r => r.retenu || r.type === "explicative") ? [el("div", {class: "st-section"}, tj("engine/static/engine/js/series_temporelles.js:412:110")), blocEffets(res)] : []),
    ...(res.glissante ? [el("div", {class: "st-section"}, tj("engine/static/engine/js/series_temporelles.js:lbl_validation_glissante")), blocGlissante(res)] : []),
    el("div", {class: "st-section"}, tj("engine/static/engine/js/series_temporelles.js:414:39")), blocDiagnostic(res, 640),
    el("div", {class: "st-section"}, tj("engine/static/engine/js/series_temporelles.js:lbl_indicateurs")), stats,
    ...(res.exploration ? [el("div", {class: "st-section"}, tj("engine/static/engine/js/series_temporelles.js:416:62")), blocExploration(res, 640)] : []),
    el("div", {class: "st-section"}, tj("engine/static/engine/js/series_temporelles.js:417:39") + prev.horizon + tj("engine/static/engine/js/series_temporelles.js:417:75")), tabPrev,
    el("details", {}, el("summary", {}, tj("engine/static/engine/js/series_temporelles.js:418:42") + res.candidats.length + ")"), tabCand),
    el("button", {class: "geo-btn st-export", onclick: () => exporter(res)}, tj("engine/static/engine/js/series_temporelles.js:419:79")));
  c._redessiner = redessiner;
  return c;
}
function rendre() {
  gyPaletteRaz();
  const zone = $("st-resultats");
  zone.textContent = "";
  const tables = (S.res && S.res.tables) || [];
  const plusieurs = tables.length > 1;
  let nb = 0;
  for (const t of tables) {
    if (plusieurs || t.vide) zone.append(el("div", {class: "st-table-titre"}, t.table));
    if (t.vide) { zone.append(el("div", {class: "geo-note"}, t.message)); continue; }
    for (const s of t.series) { const c = carte(s, false); zone.append(c); nb++; if (c._redessiner) c._redessiner(); }
  }
  if (S.res && S.res.comparaisons) zone.append(blocComparaison(S.res.comparaisons));
  if (!nb && tables.length) message(tj("engine/static/engine/js/series_temporelles.js:435:38"), true);
}

// ── Démarrage ────────────────────────────────────────────────────────────────
dessinerTables();
const sauve = ctx.restaurer();   // cases cochées, colonnes de dates, paramètres : remis comme à la dernière visite
$("btn-analyser").onclick = () => analyser(false);
$("btn-demo").onclick = () => analyser(true);
let _minuterie = null;
window.addEventListener("resize", () => { clearTimeout(_minuterie); _minuterie = setTimeout(() => { if (S.res) rendre(); }, 150); });
if (sauve.analyse && sauve.demo) analyser(true);
else if (sauve.analyse || C.tableCompatible) analyser(false);
else message(tj("engine/static/engine/js/series_temporelles.js:447:15") + C.messageStructure + (S.tables.length ? tj("engine/static/engine/js/series_temporelles.js:447:103") : ""), true);
})();
