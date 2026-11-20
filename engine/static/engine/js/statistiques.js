// STATISTIQUES : page dans le châssis du dashboard BI. Tous les calculs viennent du serveur (engine/statistiques*.py) : ici on ne fait que
// CHOISIR les tables, colonnes, échantillon et test, LANCER le calcul et AFFICHER le résultat (tableaux et graphiques SVG sans dépendance).
(function () {
"use strict";
const C = window.STATS;
const TABLES = JSON.parse(document.getElementById("sx-donnees-tables").textContent || "[]");
const CAT = JSON.parse(document.getElementById("sx-donnees-catalogue").textContent || "{}");
const GENRES = CAT.genres || {};
const S = {tests: CAT.tests || [], lois: CAT.lois || []};
const ctx = GYContexte.init({page: "stats", tableId: C.tableId});
const COUL = {barre: gyCouleur("--accent","#6bcfff"), ligne: gyCouleur("--forme-orange","#f5a524"), ok: gyCouleur("--ok","#2ecc71"), mauvais: gyCouleur("--danger","#e74c3c"), texte: gyCouleur("--texte-clair","#d4d4d4")};
// Couleurs des graphiques : 24 couleurs hexadécimales au plus (gyPalette, voir gy_couleur.js), une par colonne / catégorie, toutes différentes.
// Chaque graphique prend les couleurs qui SUIVENT celles du graphique précédent (retour au début après 24).
let _curseur = 0;
function palette(n) { const p = gyPalette(n, _curseur); _curseur = (_curseur + p.length) % 24; return p; }
function degrade(c1, c2, c3) {   // dégradé à trois teintes hexadécimales : t de 0 à 1 -> « rgb(r,g,b) »
  const h = c => [1, 3, 5].map(i => parseInt(c.slice(i, i + 2), 16)), A = [h(c1), h(c2), h(c3)];
  return t => { t = Math.max(0, Math.min(1, t)); const k = t < .5 ? 0 : 1, u = (t - k * .5) * 2, a = A[k], b = A[k + 1]; return "rgb(" + a.map((v, i) => Math.round(v + (b[i] - v) * u)).join(",") + ")"; };
}

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
  clearTimeout(t._t); t._t = setTimeout(() => { t.style.display = "none"; }, 4500);
}
async function api(chemin, params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) if (v !== null && v !== undefined && v !== "") q.set(k, v);
  const res = await fetch(`/api/tables/${C.tableId}/statistiques/${chemin}/?` + q.toString(), {headers: {"Accept": "application/json"}});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.erreur || (tj("engine/static/engine/js/statistiques.js:36:49") + res.status));
  return data;
}
const nf = new Intl.NumberFormat((window.I18N_LOCALE||"fr-FR"), {maximumSignificantDigits: 6});
const fmt = v => (v == null || (typeof v === "number" && !isFinite(v))) ? "—" : (typeof v === "number" ? nf.format(v) : String(v));
const fmtPct = v => v == null ? "—" : nf.format(Math.round(v * 100) / 100) + " %";
const fmtP = p => p == null ? "—" : (p < 1e-4 ? "< 0,0001" : p.toFixed(4).replace(".", ","));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
function chargement(zone, msg) { zone.textContent = ""; zone.append(el("div", {class: "sx-vide"}, msg || tj("engine/static/engine/js/statistiques.js:44:107"))); }
function erreur(zone, e) { zone.textContent = ""; zone.append(el("div", {class: "sx-erreur"}, e.message || String(e))); }

// ── Sélection : tables, colonnes, types, échantillon ─────────────────────────
const E = {genres: {}};   // E.genres["id:slug"] = genre forcé
function dessinerTables() {
  const zone = $("sx-tables");
  zone.textContent = "";
  if (!TABLES.length) { zone.append(el("div", {class: "geo-note"}, tj("engine/static/engine/js/statistiques.js:52:69"))); return; }
  for (const t of TABLES) {
    const courante = t.id === C.tableId;
    const entete = el("label", {class: "geo-case", title: tj("engine/static/engine/js/statistiques.js:55:60")},
      el("input", {type: "checkbox", "data-tout": t.id, onchange: e => { zone.querySelectorAll(`input[data-table="${t.id}"]`).forEach(i => { i.checked = e.target.checked; }); majMemoire(); }}),
      el("b", {}, " " + t.nom + (courante ? " (ouverte)" : "")));
    const bloc = el("div", {class: "st-table"}, entete);
    for (const c of t.colonnes) {
      const sel = el("select", {class: "sx-type", "data-genre": t.id + ":" + c.slug, title: tj("engine/static/engine/js/statistiques.js:60:94"), onchange: e => { const k = t.id + ":" + c.slug; if (e.target.value) E.genres[k] = e.target.value; else delete E.genres[k]; }},
        el("option", {value: ""}, "auto"), ...Object.entries(GENRES).map(([k, n]) => el("option", {value: k}, n)));
      bloc.append(el("label", {class: "geo-case st-col"},
        el("input", {type: "checkbox", "data-table": t.id, "data-slug": c.slug, onchange: majMemoire}), " " + c.nom, sel));
    }
    zone.append(bloc);
    if (courante) { entete.firstChild.checked = true; bloc.querySelectorAll("input[data-table]").forEach(i => { i.checked = true; }); }
  }
  if (window.GYFiltreTables) GYFiltreTables.installer(zone, {courant: C.tableId});   // une seule table affichée à la fois (filtre)
  majMemoire();
}
function colonnesCochees() {
  return Array.from(document.querySelectorAll("#sx-tables input[data-table]:checked")).map(i => i.getAttribute("data-table") + ":" + i.getAttribute("data-slug"));
}
function tablesRetenues() {
  const ids = new Set(colonnesCochees().map(r => Number(r.split(":")[0])));
  ids.add(C.tableId);
  return Array.from(ids);
}
function toutesColonnes() {   // colonnes proposées comme variables des tests : celles des tables retenues + « Table d'origine »
  const ids = tablesRetenues();
  const liste = [];
  for (const t of TABLES.filter(t => ids.includes(t.id))) {
    for (const c of t.colonnes) liste.push({ref: t.id + ":" + c.slug, nom: c.nom + (ids.length > 1 ? " — " + t.nom : ""), famille: c.famille});
  }
  if (ids.length > 1) for (const id of ids) liste.push({ref: id + ":__table__", nom: tj("engine/static/engine/js/statistiques.js:84:87") + (TABLES.find(t => t.id === id) || {nom: id}).nom, famille: "quali"});
  return liste;
}
function majMemoire() {
  const ids = tablesRetenues();
  const sig = new Set(TABLES.filter(t => ids.includes(t.id)).map(t => t.signature));
  $("sx-memoire").textContent = ids.length < 2 ? tj("engine/static/engine/js/statistiques.js:90:51")
    : (sig.size === 1 ? tj("engine/static/engine/js/statistiques.js:91:26") + ids.length + tj("engine/static/engine/js/statistiques.js:91:76")
      : tj("engine/static/engine/js/statistiques.js:92:10"));
  const strate = $("e-strate"), cur = strate.value;
  strate.textContent = "";
  strate.append(el("option", {value: ""}, tj("engine/static/engine/js/statistiques.js:95:44")));
  for (const c of toutesColonnes().filter(c => c.famille !== "quanti" && !c.ref.endsWith(":__table__"))) strate.append(el("option", {value: c.ref}, c.nom));
  strate.value = cur;
  const par = $("d-par"), pc = par.value;
  par.textContent = "";
  par.append(el("option", {value: ""}, tj("engine/static/engine/js/statistiques.js:100:41")));
  for (const c of toutesColonnes().filter(c => c.famille !== "quanti")) par.append(el("option", {value: c.ref}, c.nom));
  par.value = pc;
  if (S.roles) majRoles();
}
function parametresCommuns() {
  const p = {tables: tablesRetenues().filter(id => id !== C.tableId).join(","), types: Object.entries(E.genres).map(([k, g]) => k + ":" + g).join(",")};
  const m = $("e-methode").value;
  if (m) {
    p.echantillon = m; p.taille = $("e-taille").value; p.graine = $("e-graine").value; p.remise = $("e-remise").checked ? "1" : "0";
    if (m === "stratifie") p.strate = $("e-strate").value;
  }
  return p;
}
function initEchantillon() {
  const sel = $("e-methode");
  for (const [k, n] of Object.entries(CAT.echantillons || {})) sel.append(el("option", {value: k}, n));
}

// ── Graphiques SVG ───────────────────────────────────────────────────────────
// Largeur des graphiques = largeur libre de la page (le SVG est ensuite mis à l'échelle à 100 % : sidebar masquée ou affichée, rien ne reste figé)
const LARG = () => { const z = document.getElementById("zone-series"); return Math.max(420, Math.min(1600, (z ? z.clientWidth : 640) - 70)); };
function cadre(w, h) { return sv("svg", {viewBox: `0 0 ${w} ${h}`, class: "sx-svg", role: "img"}); }

// Légende : chaque couleur du graphique porte son libellé. entrees = [[couleur, libellé, forme]] (forme : "carre" | "ligne" | "point"), posée sous le tracé ; la hauteur du cadre grandit d'autant.
function legende(svg, entrees, x0) {
  const vb = svg.getAttribute("viewBox").split(" ").map(Number), W = vb[2], H = vb[3], g0 = x0 == null ? 8 : x0;
  let x = g0, y = H + 4, lignes = 1;
  const liste = entrees.length > 48 ? entrees.slice(0, 47).concat([["#9ca3af", "… +" + (entrees.length - 47), "carre"]]) : entrees;
  liste.forEach(([coul, lib, forme]) => {
    lib = String(lib);
    const w = 22 + lib.length * 5.6;
    if (x + w > W - 4 && x > g0) { x = g0; y += 14; lignes++; }
    svg.append(forme === "ligne" ? sv("line", {x1: x, x2: x + 12, y1: y + 5, y2: y + 5, stroke: coul, "stroke-width": 2})
      : forme === "point" ? sv("circle", {cx: x + 5, cy: y + 5, r: 3.4, fill: coul}) : sv("rect", {x, y, width: 10, height: 10, fill: coul, opacity: .9}),
      sv("text", {x: x + 15, y: y + 9, fill: gyCouleur("--texte-clair", "#d4d4d4"), "font-size": 10}, lib));
    x += w;
  });
  svg.setAttribute("viewBox", `0 0 ${W} ${H + 8 + lignes * 14}`);
  return svg;
}
function axeX(svg, x0, x1, y, lo, hi, n = 5) {
  svg.append(sv("line", {x1: x0, x2: x1, y1: y, y2: y, stroke: gyCouleur("--bordure-forte","#666")}));
  for (let i = 0; i <= n; i++) {
    const v = lo + (hi - lo) * i / n, x = x0 + (x1 - x0) * i / n;
    svg.append(sv("line", {x1: x, x2: x, y1: y, y2: y + 4, stroke: gyCouleur("--bordure-forte","#666")}), sv("text", {x, y: y + 16, "text-anchor": "middle", fill: gyCouleur("--texte-discret","#999"), "font-size": 10}, fmt(Number(v.toPrecision(3)))));
  }
}
function axeY(svg, x, y0, y1, lo, hi, n = 4) {
  svg.append(sv("line", {x1: x, x2: x, y1: y0, y2: y1, stroke: gyCouleur("--bordure-forte","#666")}));
  for (let i = 0; i <= n; i++) {
    const v = lo + (hi - lo) * i / n, y = y0 + (y1 - y0) * i / n;
    svg.append(sv("line", {x1: x - 4, x2: x, y1: y, y2: y, stroke: gyCouleur("--bordure-forte","#666")}), sv("text", {x: x - 6, y: y + 3, "text-anchor": "end", fill: gyCouleur("--texte-discret","#999"), "font-size": 10}, fmt(Number(v.toPrecision(3)))));
  }
}
function histogramme(h) {
  const W = LARG(), H = 220, L = 46, R = 10, T = 10, B = 28, svg = cadre(W, H);
  const maxi = Math.max(...h.effectifs, 1), lo = h.bornes[0], hi = h.bornes[h.bornes.length - 1], x = v => L + (v - lo) / ((hi - lo) || 1) * (W - L - R);
  axeY(svg, L, H - B, T, 0, maxi); axeX(svg, L, W - R, H - B, lo, hi);
  const pal = palette(h.effectifs.length);
  h.effectifs.forEach((e, i) => {
    const x0 = x(h.bornes[i]), x1 = x(h.bornes[i + 1]), y = H - B - e / maxi * (H - B - T);
    svg.append(sv("rect", {x: x0 + 0.5, y, width: Math.max(1, x1 - x0 - 1), height: H - B - y, fill: pal[i], opacity: .85}, sv("title", {}, tj("engine/static/engine/js/statistiques.js:borne_a_borne", {v1: fmt(h.bornes[i]), v2: fmt(h.bornes[i + 1]), v3: e}))));
  });
  legende(svg, h.effectifs.map((e, i) => [pal[i], fmt(h.bornes[i]) + " – " + fmt(h.bornes[i + 1]), "carre"]));   // une couleur par classe : sa borne inférieure et supérieure
  return svg;
}
function barres(etiq, vals, horizontal) {
  const n = etiq.length, maxi = Math.max(...vals, 1), pal = palette(n);
  if (horizontal) {
    const ligne = 22, W = LARG(), L = 150, H = n * ligne + 12, svg = cadre(W, H);
    etiq.forEach((e, i) => {
      const w = vals[i] / maxi * (W - L - 70);
      svg.append(sv("text", {x: L - 6, y: 8 + i * ligne + 13, "text-anchor": "end", fill: COUL.texte, "font-size": 11}, e.length > 22 ? e.slice(0, 21) + "…" : e),
        sv("rect", {x: L, y: 8 + i * ligne + 2, width: Math.max(1, w), height: ligne - 6, fill: pal[i], opacity: .85}),
        sv("text", {x: L + w + 5, y: 8 + i * ligne + 13, fill: gyCouleur("--texte-discret","#999"), "font-size": 10}, fmt(vals[i])));
    });
    legende(svg, etiq.map((e, i) => [pal[i], String(e).slice(0, 24), "carre"]));
    return svg;
  }
  const W = LARG(), H = 200, L = 46, B = 40, T = 10, svg = cadre(W, H), pas = (W - L - 10) / n;
  axeY(svg, L, H - B, T, 0, maxi);
  etiq.forEach((e, i) => {
    const h = vals[i] / maxi * (H - B - T);
    svg.append(sv("rect", {x: L + i * pas + pas * .1, y: H - B - h, width: pas * .8, height: h, fill: pal[i], opacity: .85}, sv("title", {}, e + " : " + fmt(vals[i]))));
    if (n <= 24 || i % Math.ceil(n / 24) === 0) svg.append(sv("text", {x: L + i * pas + pas / 2, y: H - B + 13, "text-anchor": "middle", fill: gyCouleur("--texte-discret","#999"), "font-size": 10}, String(e).slice(0, 8)));
  });
  legende(svg, etiq.map((e, i) => [pal[i], String(e).slice(0, 24), "carre"]));
  return svg;
}
function secteurs(etiq, vals) {   // anneau de répartition (12 parts au plus, légende à droite)
  const W = 440, H = 210, cx = 105, cy = 105, r = 95, ri = 50, tot = vals.reduce((a, b) => a + b, 0) || 1, svg = cadre(W, H), P = (R, a) => `${cx + R * Math.cos(a)} ${cy + R * Math.sin(a)}`;
  svg.style.maxWidth = "460px";
  const pal = palette(Math.min(vals.length, 12));
  let a0 = -Math.PI / 2;
  vals.slice(0, 12).forEach((v, i) => {
    const a1 = a0 + Math.min(v / tot, 0.9999) * 2 * Math.PI, c = pal[i];
    svg.append(sv("path", {d: `M ${P(r, a0)} A ${r} ${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${P(r, a1)} L ${P(ri, a1)} A ${ri} ${ri} 0 ${a1 - a0 > Math.PI ? 1 : 0} 0 ${P(ri, a0)} Z`, fill: c}, sv("title", {}, `${etiq[i]} : ${fmt(v)}`)),
      sv("rect", {x: 225, y: 8 + i * 16, width: 10, height: 10, fill: c}), sv("text", {x: 241, y: 17 + i * 16, fill: COUL.texte, "font-size": 11}, `${String(etiq[i]).slice(0, 20)} (${fmt(Math.round(v / tot * 1000) / 10)} %)`));
    a0 = a1;
  });
  return svg;
}
function pareto(etiq, vals, cumuls) {   // barres décroissantes + courbe des cumuls (0 à 100 %)
  const n = etiq.length, W = LARG(), H = 220, L = 46, R = 40, T = 10, B = 40, svg = cadre(W, H), pas = (W - L - R) / n, maxi = Math.max(...vals, 1), y = c => H - B - c / 100 * (H - B - T), pal = palette(n + 1);
  axeY(svg, L, H - B, T, 0, maxi);
  vals.forEach((v, i) => svg.append(sv("rect", {x: L + i * pas + pas * .1, y: H - B - v / maxi * (H - B - T), width: pas * .8, height: v / maxi * (H - B - T), fill: pal[i], opacity: .85}, sv("title", {}, etiq[i] + " : " + fmt(v))),
    ...(n <= 24 || i % Math.ceil(n / 24) === 0 ? [sv("text", {x: L + i * pas + pas / 2, y: H - B + 13, "text-anchor": "middle", fill: gyCouleur("--texte-discret","#999"), "font-size": 10}, String(etiq[i]).slice(0, 8))] : [])));
  svg.append(gyLigneDegrade(cumuls.map((c, i) => [L + i * pas + pas / 2, y(c)]), pal[n], {"stroke-width": 2}));   // cumul = évolution : dégradé du plus sombre au plus clair
  for (const c of [0, 50, 100]) svg.append(sv("text", {x: W - R + 4, y: y(c) + 3, fill: pal[n], "font-size": 10}, c + " %"));
  legende(svg, etiq.map((e, i) => [pal[i], String(e).slice(0, 24), "carre"]).concat([[pal[n], "Cumul des effectifs (%, axe de droite)", "ligne"]]));
  return svg;
}
function ogive(pts) {   // fonction de répartition empirique : part des valeurs inférieures ou égales à x
  const W = LARG(), H = 200, L = 46, R = 10, T = 10, B = 28, svg = cadre(W, H), lo = pts[0][0], hi = pts[pts.length - 1][0], x = v => L + (v - lo) / ((hi - lo) || 1) * (W - L - R), y = c => H - B - c / 100 * (H - B - T);
  axeY(svg, L, H - B, T, 0, 100); axeX(svg, L, W - R, H - B, lo, hi);
  const cOgive = palette(1)[0]; legende(svg, [[cOgive, "Part des valeurs ≤ x (%)", "ligne"]]);
  svg.append(gyLigneDegrade(pts.map(p => [x(p[0]), y(p[1])]), cOgive, {"stroke-width": 2}));   // répartition cumulée = évolution : dégradé du plus sombre au plus clair
  return svg;
}
function empilees(lignes, colonnes, eff) {   // barres empilées à 100 % : composition de chaque ligne du tableau croisé
  const ligne = 22, W = LARG(), L = 150, H = lignes.length * ligne + 34, svg = cadre(W, H), pal = palette(colonnes.length), coul = j => pal[j];
  lignes.forEach((l, i) => {
    const tot = eff[i].reduce((a, b) => a + b, 0) || 1; let x = L;
    svg.append(sv("text", {x: L - 6, y: 8 + i * ligne + 13, "text-anchor": "end", fill: COUL.texte, "font-size": 11}, String(l).slice(0, 22)));
    eff[i].forEach((e, j) => { const w = e / tot * (W - L - 10); svg.append(sv("rect", {x, y: 8 + i * ligne + 2, width: Math.max(0, w), height: ligne - 6, fill: coul(j)}, sv("title", {}, `${l} / ${colonnes[j]} : ${e} (${fmt(Math.round(e / tot * 1000) / 10)} %)`))); x += w; });
  });
  colonnes.slice(0, Math.max(1, Math.floor((W - L) / 90))).forEach((c, j) => svg.append(sv("rect", {x: L + j * 90, y: H - 18, width: 9, height: 9, fill: coul(j)}), sv("text", {x: L + j * 90 + 13, y: H - 10, fill: COUL.texte, "font-size": 10}, String(c).slice(0, 10))));
  return svg;
}
function intervalles(gs) {   // un groupe par ligne : étendue (trait), moyenne ± écart-type (barre), moyenne (trait orange), médiane (point)
  gs = gs.filter(g => Number.isFinite(g.min) && Number.isFinite(g.max) && Number.isFinite(g.moyenne));
  const ng = gs.length, pal = palette(4 * ng);   // par groupe : barre ± écart-type (teinte du groupe), étendue, moyenne, médiane : chacun sa couleur
  const ligne = 26, W = LARG(), L = 150, R = 20, H = gs.length * ligne + 34, svg = cadre(W, H), lo = Math.min(...gs.map(g => g.min)), hi = Math.max(...gs.map(g => g.max)), x = v => L + (v - lo) / ((hi - lo) || 1) * (W - L - R);
  axeX(svg, L, W - R, H - 24, lo, hi);
  gs.forEach((g, i) => {
    const y = 12 + i * ligne, sd = g.ecart_type || 0;
    svg.append(sv("text", {x: L - 6, y: y + 4, "text-anchor": "end", fill: pal[i], "font-size": 11}, String(g.nom).slice(0, 22)), sv("line", {x1: x(g.min), x2: x(g.max), y1: y, y2: y, stroke: pal[ng + i]}),
      sv("rect", {x: x(g.moyenne - sd), y: y - 7, width: Math.max(1, x(g.moyenne + sd) - x(g.moyenne - sd)), height: 14, fill: pal[i], opacity: .55}), sv("line", {x1: x(g.moyenne), x2: x(g.moyenne), y1: y - 9, y2: y + 9, stroke: pal[2 * ng + i], "stroke-width": 2}),
      ...(Number.isFinite(g.mediane) ? [sv("circle", {cx: x(g.mediane), cy: y, r: 3, fill: pal[3 * ng + i]})] : []));
  });
  legende(svg, [[pal[ng], "Étendue min–max (trait fin)", "ligne"], [pal[2 * ng], "Moyenne (trait épais)", "ligne"], [pal[3 * ng], "Médiane (point)", "point"]].concat(gs.map((g, i) => [pal[i], g.nom + " : moyenne ± écart-type", "carre"])));
  return svg;
}
function observeAttendu(tab) {   // effectifs observés (bleu) et attendus (orange) côte à côte
  const n = tab.length, W = LARG(), H = 200, L = 46, B = 40, T = 10, svg = cadre(W, H), pas = (W - L - 10) / n, maxi = Math.max(...tab.map(t => Math.max(t.observe, t.attendu)), 1), h = v => v / maxi * (H - B - T), pal = palette(2 * n);
  axeY(svg, L, H - B, T, 0, maxi);
  tab.forEach((t, i) => {
    svg.append(sv("rect", {x: L + i * pas + pas * .08, y: H - B - h(t.observe), width: pas * .4, height: h(t.observe), fill: pal[i], opacity: .85}, sv("title", {}, tj("engine/static/engine/js/statistiques.js:221:170", {v1: t.modalite, v2: fmt(t.observe)}))),
      sv("rect", {x: L + i * pas + pas * .5, y: H - B - h(t.attendu), width: pas * .4, height: h(t.attendu), fill: pal[n + i], opacity: .85}, sv("title", {}, tj("engine/static/engine/js/statistiques.js:lbl_attendu", {v1: t.modalite, v2: fmt(t.attendu)}))));
    if (n <= 24 || i % Math.ceil(n / 24) === 0) svg.append(sv("text", {x: L + i * pas + pas / 2, y: H - B + 13, "text-anchor": "middle", fill: gyCouleur("--texte-discret","#999"), "font-size": 10}, String(t.modalite).slice(0, 8)));
  });
  svg.append(sv("text", {x: W - 150, y: 12, fill: COUL.texte, "font-size": 11}, tj("engine/static/engine/js/statistiques.js:225:82")), sv("text", {x: W - 80, y: 12, fill: COUL.texte, "font-size": 11}, tj("engine/static/engine/js/statistiques.js:lbl_attendu")));
  legende(svg, tab.map((t, i) => [pal[i], t.modalite + " – observé", "carre"]).concat(tab.map((t, i) => [pal[n + i], t.modalite + " – attendu", "carre"])));
  return svg;
}
function boite(b, ab) {
  const W = LARG(), H = 70, L = 20, R = 20, svg = cadre(W, H), ex = b.aberrantes || [], pal = palette(4 + ex.length);   // moustache basse, moustache haute, boîte, médiane, puis chaque valeur aberrante
  const pts = [b.min, b.max, ...ex], lo = Math.min(...pts), hi = Math.max(...pts), x = v => L + (v - lo) / ((hi - lo) || 1) * (W - L - R);
  axeX(svg, L, W - R, 44, lo, hi);
  svg.append(sv("line", {x1: x(b.min), x2: x(b.q1), y1: 22, y2: 22, stroke: pal[0], "stroke-width": 2}), sv("line", {x1: x(b.q3), x2: x(b.max), y1: 22, y2: 22, stroke: pal[1], "stroke-width": 2}),
    sv("line", {x1: x(b.min), x2: x(b.min), y1: 14, y2: 30, stroke: pal[0], "stroke-width": 2}), sv("line", {x1: x(b.max), x2: x(b.max), y1: 14, y2: 30, stroke: pal[1], "stroke-width": 2}),
    sv("rect", {x: x(b.q1), y: 10, width: Math.max(1, x(b.q3) - x(b.q1)), height: 24, fill: pal[2], opacity: .55, stroke: pal[2]}),
    sv("line", {x1: x(b.mediane), x2: x(b.mediane), y1: 10, y2: 34, stroke: pal[3], "stroke-width": 2}));
  ex.forEach((v, i) => svg.append(sv("circle", {cx: x(v), cy: 22, r: 3, fill: "none", stroke: pal[4 + i], "stroke-width": 1.6})));
  legende(svg, [[pal[0], "Minimum → Q1", "ligne"], [pal[1], "Q3 → maximum", "ligne"], [pal[2], "Boîte Q1–Q3", "carre"], [pal[3], "Médiane", "ligne"]].concat(ex.length ? [[pal[4], "Valeurs aberrantes", "point"]] : []), L);
  return svg;
}
function nuage(pts, nx, ny, droite) {
  const W = LARG(), H = 260, L = 52, R = 10, T = 10, B = 34, svg = cadre(W, H);
  if (!pts.x.length) return svg;
  const pal = palette(pts.x.length), lc = palette(2);   // un point = une couleur (24 teintes en boucle) ; courbe ajustée et droite : chacune la sienne
  const lx = Math.min(...pts.x), hx = Math.max(...pts.x), ly = Math.min(...pts.y), hy = Math.max(...pts.y);
  const x = v => L + (v - lx) / ((hx - lx) || 1) * (W - L - R), y = v => H - B - (v - ly) / ((hy - ly) || 1) * (H - B - T);
  axeX(svg, L, W - R, H - B, lx, hx); axeY(svg, L, H - B, T, ly, hy);
  pts.x.forEach((v, i) => svg.append(sv("circle", {cx: x(v), cy: y(pts.y[i]), r: 2.6, fill: pal[i], opacity: .85})));
  if (pts.courbe) {   // courbe ajustée (polynomiale, non linéaire) : tracé coupé là où elle sort largement du nuage
    const marge = (hy - ly) || 1; let d = "", pose = false;
    pts.courbe.x.forEach((v, i) => { const w = pts.courbe.y[i], dedans = w >= ly - marge && w <= hy + marge; d += dedans ? (pose ? "L" : "M") + x(v).toFixed(1) + " " + y(w).toFixed(1) : ""; pose = dedans; });
    if (d) svg.append(sv("path", {d, fill: "none", stroke: lc[0], "stroke-width": 2}));
  }
  if (droite) svg.append(sv("line", {x1: x(lx), x2: x(hx), y1: y(droite.ordonnee + droite.pente * lx), y2: y(droite.ordonnee + droite.pente * hx), stroke: lc[1], "stroke-width": 2}));
  legende(svg, [[pal[0], "Observations", "point"]].concat(pts.courbe ? [[lc[0], "Courbe ajustée", "ligne"]] : [], droite ? [[lc[1], "Droite de régression", "ligne"]] : []));
  svg.append(sv("text", {x: (L + W) / 2, y: H - 4, "text-anchor": "middle", fill: gyCouleur("--texte-discret","#999"), "font-size": 11}, nx), sv("text", {x: 12, y: 14, fill: gyCouleur("--texte-discret","#999"), "font-size": 11}, ny));
  return svg;
}
let _idCarte = 0;
function carteChaleur(c) {   // carte de chaleur : valeur ajustée sur une grille régulière (couleur), observations en points
  const W = LARG(), H = 330, L = 52, R = 74, T = 10, B = 34, pw = W - L - R, ph = H - B - T, svg = cadre(W, H);
  const gx = c.x, gy = c.y, zs = c.z.flat().filter(u => u != null && isFinite(u));
  if (!gx.length || !gy.length || !zs.length) return svg;
  const lz = Math.min(...zs), hz = Math.max(...zs), lx = gx[0], hx = gx[gx.length - 1], ly = gy[0], hy = gy[gy.length - 1];
  const x = v => L + (v - lx) / ((hx - lx) || 1) * pw, y = v => H - B - (v - ly) / ((hy - ly) || 1) * ph;
  // bas, milieu, haut : trois teintes hexadécimales de la palette, différentes pour chaque carte
  const tri = palette(3), teinte = degrade(tri[0], tri[1], tri[2]);
  const id = "sx-clip-" + (++_idCarte), cw = pw / Math.max(1, gx.length - 1), ch = ph / Math.max(1, gy.length - 1);
  svg.append(sv("clipPath", {id}, sv("rect", {x: L, y: T, width: pw, height: ph})));
  const g = sv("g", {"clip-path": "url(#" + id + ")"});
  c.z.forEach((lig, j) => lig.forEach((u, i) => {
    if (u == null || !isFinite(u)) return;
    g.append(sv("rect", {x: (x(gx[i]) - cw / 2).toFixed(1), y: (y(gy[j]) - ch / 2).toFixed(1), width: (cw + .6).toFixed(1), height: (ch + .6).toFixed(1), fill: teinte((u - lz) / ((hz - lz) || 1))}));
  }));
  svg.append(g);
  axeX(svg, L, W - R, H - B, lx, hx); axeY(svg, L, H - B, T, ly, hy);
  const pp = c.pts ? palette(c.pts.x.length) : [];
  if (c.pts) c.pts.x.forEach((v, i) => svg.append(sv("circle", {cx: x(v), cy: y(c.pts.y[i]), r: 2.4, fill: pp[i], stroke: gyCouleur("--bordure-forte","#222"), "stroke-width": .8, opacity: .85})));
  const bx = W - R + 18, nb = 24;       // échelle de couleur
  for (let i = 0; i < nb; i++) svg.append(sv("rect", {x: bx, y: T + ph * i / nb, width: 12, height: ph / nb + .6, fill: teinte(1 - i / (nb - 1))}));
  svg.append(sv("text", {x: bx + 16, y: T + 9, fill: gyCouleur("--texte-discret","#999"), "font-size": 10}, fmt(Number(hz.toPrecision(3)))), sv("text", {x: bx + 16, y: T + ph, fill: gyCouleur("--texte-discret","#999"), "font-size": 10}, fmt(Number(lz.toPrecision(3)))));
  svg.append(sv("text", {x: (L + W - R) / 2, y: H - 4, "text-anchor": "middle", fill: gyCouleur("--texte-discret","#999"), "font-size": 11}, c.nom_x), sv("text", {x: 12, y: 14, fill: gyCouleur("--texte-discret","#999"), "font-size": 11}, c.nom_y));
  legende(svg, [[tri[2], "Valeurs hautes de " + (c.nom_z || "la mesure"), "carre"], [tri[0], "Valeurs basses de " + (c.nom_z || "la mesure"), "carre"]].concat(c.pts ? [[pp[0], "Observations", "point"]] : []));
  return svg;
}
function surface3D(c) {   // surface en perspective simple, faces triées de l'arrière vers l'avant ; glisser pour tourner (souris, doigt)
  const W = LARG(), H = 360, svg = cadre(W, H), gx = c.x, gy = c.y, nx = gx.length, ny = gy.length;
  const zs = c.z.flat().filter(u => u != null && isFinite(u));
  if (nx < 2 || ny < 2 || !zs.length) return svg;
  const lz = Math.min(...zs), hz = Math.max(...zs), lx = gx[0], hx = gx[nx - 1], ly = gy[0], hy = gy[ny - 1];
  const nrm = (v, a, b) => ((v - a) / ((b - a) || 1)) * 2 - 1;      // cube [-1, 1]
  const tri = palette(3), teinte = degrade(tri[0], tri[1], tri[2]);
  const etat = {az: -0.9, el: 0.55}, calque = sv("g", {});
  const P = (X, Y, Zv) => {   // rotation autour de l'axe vertical (azimut) puis inclinaison (élévation) ; renvoie [px, py, profondeur]
    const ca = Math.cos(etat.az), sa = Math.sin(etat.az), ce = Math.cos(etat.el), se = Math.sin(etat.el);
    const x1 = X * ca - Y * sa, y1 = X * sa + Y * ca, y2 = y1 * ce - Zv * se, z2 = y1 * se + Zv * ce, f = 1 / (1 + 0.18 * y2);
    return [W / 2 + x1 * 150 * f, H / 2 + 20 - z2 * 120 * f, y2];
  };
  function tracer() {
    calque.textContent = "";
    const faces = [];
    for (let j = 0; j < ny - 1; j++) for (let i = 0; i < nx - 1; i++) {
      const zz = [c.z[j][i], c.z[j][i + 1], c.z[j + 1][i + 1], c.z[j + 1][i]];
      if (zz.some(u => u == null || !isFinite(u))) continue;
      const pts = [[i, j], [i + 1, j], [i + 1, j + 1], [i, j + 1]].map(([a, b], q) => P(nrm(gx[a], lx, hx), nrm(gy[b], ly, hy), nrm(zz[q], lz, hz)));
      faces.push({pts, prof: pts.reduce((u, p) => u + p[2], 0) / 4, t: (zz.reduce((u, v) => u + v, 0) / 4 - lz) / ((hz - lz) || 1)});
    }
    faces.sort((u, v) => v.prof - u.prof);
    faces.forEach(q => calque.append(sv("polygon", {points: q.pts.map(p => p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" "), fill: teinte(q.t), stroke: "color-mix(in srgb,var(--voile,#000) 25%,transparent)", "stroke-width": .4})));
    const pied = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([a, b]) => P(a, b, -1));      // cadre au sol
    calque.append(sv("polygon", {points: pied.map(p => p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" "), fill: "none", stroke: gyCouleur("--bordure-forte","#666"), "stroke-width": 1}));
    const lab = (X, Y, t) => { const p = P(X, Y, -1); return sv("text", {x: p[0].toFixed(1), y: (p[1] + 14).toFixed(1), "text-anchor": "middle", fill: gyCouleur("--texte-discret","#999"), "font-size": 11}, t); };
    calque.append(lab(0, -1.25, c.nom_x + " (" + fmt(Number(lx.toPrecision(3))) + " → " + fmt(Number(hx.toPrecision(3))) + ")"), lab(1.3, 0, c.nom_y + " (" + fmt(Number(ly.toPrecision(3))) + " → " + fmt(Number(hy.toPrecision(3))) + ")"));
  }
  svg.append(calque);
  svg.append(sv("text", {x: 10, y: 16, fill: gyCouleur("--texte-discret","#999"), "font-size": 11}, c.nom_z + " : " + fmt(Number(lz.toPrecision(3))) + " (" + tri[0] + ") → " + fmt(Number(hz.toPrecision(3))) + " (" + tri[2] + ")"));
  legende(svg, [[tri[2], "Valeurs hautes de " + (c.nom_z || "la mesure"), "carre"], [tri[0], "Valeurs basses de " + (c.nom_z || "la mesure"), "carre"]]);
  svg.style.touchAction = "none"; svg.style.cursor = "grab";
  let ancre = null;
  svg.addEventListener("pointerdown", e => { ancre = {x: e.clientX, y: e.clientY, az: etat.az, el: etat.el}; try { svg.setPointerCapture(e.pointerId); } catch (_) {} });
  svg.addEventListener("pointermove", e => { if (!ancre) return; etat.az = ancre.az + (e.clientX - ancre.x) * 0.01; etat.el = clamp(ancre.el + (e.clientY - ancre.y) * 0.01, 0.05, 1.5); tracer(); });
  const fin = () => { ancre = null; };
  svg.addEventListener("pointerup", fin); svg.addEventListener("pointercancel", fin);
  tracer();
  return svg;
}
function qq(pts) {
  const W = 300, H = 260, L = 40, R = 10, T = 10, B = 30, svg = cadre(W, H), pal = palette(pts.length), lc = palette(1);
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]), lo = Math.min(...xs, ...ys), hi = Math.max(...xs, ...ys);
  const x = v => L + (v - lo) / ((hi - lo) || 1) * (W - L - R), y = v => H - B - (v - lo) / ((hi - lo) || 1) * (H - B - T);
  axeX(svg, L, W - R, H - B, lo, hi, 4); axeY(svg, L, H - B, T, lo, hi, 4);
  svg.append(sv("line", {x1: x(lo), x2: x(hi), y1: y(lo), y2: y(hi), stroke: lc[0], "stroke-dasharray": "4 3"}));
  pts.forEach((p, i) => svg.append(sv("circle", {cx: x(p[0]), cy: y(p[1]), r: 2.4, fill: pal[i]})));
  svg.append(sv("text", {x: W / 2, y: H - 4, "text-anchor": "middle", fill: gyCouleur("--texte-discret","#999"), "font-size": 10}, tj("engine/static/engine/js/statistiques.js:327:104")));
  legende(svg, [[pal[0], "Quantiles observés", "point"], [lc[0], "Droite de la loi normale", "ligne"]]);
  return svg;
}
function courbeLoi(c, m) {   // densité ou masse, avec zones P(X ≤ x) et P(a ≤ X ≤ b) colorées
  const W = LARG(), H = 240, L = 50, R = 10, T = 12, B = 28, svg = cadre(W, H);
  const xs = c.x, ys = c.y.map(v => v == null ? 0 : v), maxi = Math.max(...ys, 1e-12) * 1.05, lo = xs[0], hi = xs[xs.length - 1], pal = palette(c.discrete ? 2 * xs.length : 2);   // discrète : une couleur par barre + une par barre de la zone ; continue : courbe + zone
  const x = v => L + (v - lo) / ((hi - lo) || 1) * (W - L - R), y = v => H - B - v / maxi * (H - B - T);
  axeX(svg, L, W - R, H - B, lo, hi); axeY(svg, L, H - B, T, 0, maxi);
  const zone = (a, b, couleur) => {
    if (c.discrete) {
      xs.forEach((v, i) => { if (v >= a && v <= b) svg.append(sv("rect", {x: x(v) - 3, y: y(ys[i]), width: 6, height: H - B - y(ys[i]), fill: pal[xs.length + i], opacity: .9})); });
    } else {
      const sel = xs.map((v, i) => [v, ys[i]]).filter(p => p[0] >= a && p[0] <= b);
      if (sel.length > 1) svg.append(sv("polygon", {points: [[sel[0][0], 0], ...sel, [sel[sel.length - 1][0], 0]].map(p => x(p[0]) + "," + y(p[1])).join(" "), fill: pal[1], opacity: .5}));
    }
  };
  if (c.discrete) xs.forEach((v, i) => svg.append(sv("rect", {x: x(v) - 3, y: y(ys[i]), width: 6, height: H - B - y(ys[i]), fill: pal[i], opacity: .6})));
  else svg.append(sv("polyline", {points: xs.map((v, i) => x(v) + "," + y(ys[i])).join(" "), fill: "none", stroke: pal[0], "stroke-width": 2}));
  if (m.a != null && m.b != null) zone(Math.min(m.a, m.b), Math.max(m.a, m.b), COUL.ok);
  else if (m.x != null) zone(-Infinity, m.x, COUL.ok);
  legende(svg, c.discrete ? [[pal[0], "Masse de probabilité (une couleur par valeur)", "carre"], [pal[xs.length], "Zone de probabilité (une couleur par valeur)", "carre"]] : [[pal[0], "Densité de la loi", "ligne"], [pal[1], "Zone de probabilité", "carre"]]);
  return svg;
}

// ── Petits composants d'affichage ────────────────────────────────────────────
function carte(titre, ...contenu) { return el("div", {class: tj("engine/static/engine/js/statistiques.js:351:63")}, el("div", {class: "st-titre"}, titre), ...contenu); }
function paires(liste) {
  const g = el("div", {class: "sx-paires"});
  for (const [k, v] of liste) if (v !== undefined) g.append(el("div", {class: "sx-paire"}, el("span", {}, k), el("b", {}, v === null ? "—" : (typeof v === "number" ? fmt(v) : v))));
  return g;
}
function tableau(entetes, lignes, classe) {
  const t = el("table", {class: "sx-table " + (classe || "")});
  t.append(el("thead", {}, el("tr", {}, ...entetes.map(h => el("th", {}, h)))));
  const tb = el("tbody", {});
  for (const l of lignes) tb.append(el("tr", {}, ...l.map(c => c instanceof Node ? el("td", {}, c) : el("td", {}, c === null || c === undefined ? "—" : (typeof c === "number" ? fmt(c) : c)))));
  t.append(tb);
  return el("div", {class: "sx-defil"}, t);
}
function liste(items, classe) { return items && items.length ? el("ul", {class: classe || "sx-lecture"}, ...items.map(i => el("li", {}, i))) : null; }
function barreFrequence(pct, couleur) { return el("div", {class: "sx-bar"}, el("div", {style: `width:${clamp(pct, 0, 100)}%` + (couleur ? `;background:${couleur}` : "")})); }

// ── Statistiques descriptives ────────────────────────────────────────────────
function carteQuanti(v) {
  const c = carte(v.nom + " ", el("span", {class: "sx-badge"}, GENRES[v.genre] || v.genre), el("small", {class: "sx-note"}, v.table ? "  · " + v.table : ""));
  if (v.erreur) { c.append(el("div", {class: "sx-erreur"}, v.erreur)); return c; }
  c.append(paires([[tj("engine/static/engine/js/statistiques.js:lbl_effectif_n"), v.n], [tj("engine/static/engine/js/statistiques.js:372:43"), v.manquants], [tj("engine/static/engine/js/statistiques.js:372:80"), v.somme], [tj("engine/static/engine/js/statistiques.js:372:100"), v.moyenne], [tj("engine/static/engine/js/statistiques.js:372:124"), v.mediane], [tj("engine/static/engine/js/statistiques.js:372:148"), v.min], [tj("engine/static/engine/js/statistiques.js:372:168"), v.max], [tj("engine/static/engine/js/statistiques.js:372:188"), v.etendue],
    ["1er quartile", v.q1], ["3e quartile", v.q3], [tj("engine/static/engine/js/statistiques.js:lbl_intervalle_interquartile"), v.iqr], [tj("engine/static/engine/js/statistiques.js:373:91"), v.ecart_type], [tj("engine/static/engine/js/statistiques.js:373:135"), v.variance], [tj("engine/static/engine/js/statistiques.js:373:175"), v.ecart_type_pop],
    [tj("engine/static/engine/js/statistiques.js:374:7"), v.cv == null ? null : fmtPct(v.cv)], [tj("engine/static/engine/js/statistiques.js:374:73"), v.erreur_standard], [tj("engine/static/engine/js/statistiques.js:374:113"), v.asymetrie], [tj("engine/static/engine/js/statistiques.js:lbl_aplatissement"), v.aplatissement],
    [tj("engine/static/engine/js/statistiques.js:375:7"), v.moyenne_geometrique], [tj("engine/static/engine/js/statistiques.js:375:55"), v.moyenne_harmonique], ...Object.entries(v.percentiles || {}).map(([k, x]) => [tj("engine/static/engine/js/statistiques.js:lbl_centile_k", {v1: k}), x]),
    [tj("engine/static/engine/js/statistiques.js:376:7"), v.denombrement.distincts], [tj("engine/static/engine/js/statistiques.js:376:57"), v.denombrement.doublons], [tj("engine/static/engine/js/statistiques.js:376:115"), v.denombrement.zeros], [tj("engine/static/engine/js/statistiques.js:376:163"), v.denombrement.negatives], [tj("engine/static/engine/js/statistiques.js:376:212"), v.denombrement.positives]]));
  if (v.ic_moyenne) c.append(el("div", {class: "sx-note"}, tj("engine/static/engine/js/statistiques.js:377:61", {v1: fmt(v.confiance * 100), v2: fmt(v.ic_moyenne[0]), v3: fmt(v.ic_moyenne[1])})));
  const l = liste(v.lecture); if (l) c.append(l);
  if (v.histogramme) c.append(el("div", {class: "sx-graphe"}, el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:lbl_histogramme")), histogramme(v.histogramme),
    el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:380:36")), tableau([tj("engine/static/engine/js/statistiques.js:lbl_de"), tj("engine/static/engine/js/statistiques.js:lbl_a"), tj("engine/static/engine/js/statistiques.js:lbl_effectif"), tj("engine/static/engine/js/statistiques.js:380:96"), tj("engine/static/engine/js/statistiques.js:380:177")], v.classes.map(k => [k.de, k.a, k.effectif, fmtPct(k.frequence), fmtPct(k.cumul)])),
    el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:381:36")), ogive(v.ogive)));
  if (v.frequences) c.append(el("div", {class: "sx-graphe"}, el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:382:93") + (v.modes || []).map(fmt).join(", ") + ")"), barres(v.frequences.map(f => fmt(f.valeur)), v.frequences.map(f => f.effectif)),
    tableau([tj("engine/static/engine/js/statistiques.js:383:15"), tj("engine/static/engine/js/statistiques.js:lbl_effectif"), tj("engine/static/engine/js/statistiques.js:383:37"), tj("engine/static/engine/js/statistiques.js:383:135")], v.frequences.map(f => [f.valeur, f.effectif, fmtPct(f.frequence), fmtPct(f.cumul)]))));
  if (v.boite) c.append(el("div", {class: "sx-graphe"}, el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:384:88", {v1: v.aberrantes.n}) + (v.aberrantes.extremes ? tj("engine/static/engine/js/statistiques.js:384:179", {v1: v.aberrantes.extremes}) : "")), boite(v.boite)));
  if (v.normalite) c.append(el("div", {class: "sx-graphe"}, el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:385:92", {v1: v.normalite.test, v2: fmtP(v.normalite.p), v3: v.normalite.conclusion})), qq(v.normalite.qq)));
  if (v.par) c.append(parGroupe(v.par));
  return c;
}
function carteDate(v) {
  const c = carte(v.nom + " ", el("span", {class: "sx-badge"}, GENRES[v.genre] || v.genre), el("small", {class: "sx-note"}, v.table ? "  · " + v.table : ""));
  if (v.erreur) { c.append(el("div", {class: "sx-erreur"}, v.erreur)); return c; }
  c.append(paires([[tj("engine/static/engine/js/statistiques.js:lbl_effectif_n"), v.n], [tj("engine/static/engine/js/statistiques.js:392:43"), v.manquants], [tj("engine/static/engine/js/statistiques.js:392:80"), v.min], [tj("engine/static/engine/js/statistiques.js:392:106"), v.max], [tj("engine/static/engine/js/statistiques.js:392:132"), v.etendue_jours], [tj("engine/static/engine/js/statistiques.js:392:170"), v.doublons],
    [tj("engine/static/engine/js/statistiques.js:393:7"), v.delai_median], [tj("engine/static/engine/js/statistiques.js:393:61"), v.delai_moyen], [tj("engine/static/engine/js/statistiques.js:393:101"), v.delai_max]]));
  const l = liste(v.lecture); if (l) c.append(l);
  for (const [titre, t, tab] of [[tj("engine/static/engine/js/statistiques.js:395:36"), v.evolution, false], [tj("engine/static/engine/js/statistiques.js:395:116"), v.par_annee, true], [tj("engine/static/engine/js/statistiques.js:395:162"), v.par_mois, true],
      [tj("engine/static/engine/js/statistiques.js:396:9"), v.par_jour, true], [tj("engine/static/engine/js/statistiques.js:396:67"), v.par_heure, true]])
    if (t && t.length > 1) c.append(el("div", {class: "sx-graphe"}, el("div", {class: "sx-sous"}, titre), barres(t.map(x => x.modalite), t.map(x => x.effectif)),
      ...(tab ? [tableau([tj("engine/static/engine/js/statistiques.js:398:28"), tj("engine/static/engine/js/statistiques.js:lbl_effectif"), tj("engine/static/engine/js/statistiques.js:398:51")], t.map(x => [x.modalite, x.effectif, fmtPct(x.frequence)]))] : [])));
  return c;
}
function carteQuali(v) {
  const c = carte(v.nom + " ", el("span", {class: "sx-badge"}, GENRES[v.genre] || v.genre), el("small", {class: "sx-note"}, v.table ? "  · " + v.table : ""));
  if (v.erreur) { c.append(el("div", {class: "sx-erreur"}, v.erreur)); return c; }
  c.append(paires([[tj("engine/static/engine/js/statistiques.js:lbl_effectif_n"), v.n], [tj("engine/static/engine/js/statistiques.js:404:43"), v.manquants], [tj("engine/static/engine/js/statistiques.js:404:80"), v.distincts], ["Mode", (v.modes || []).join(", ")], [tj("engine/static/engine/js/statistiques.js:lbl_entropie_bits"), v.entropie], [tj("engine/static/engine/js/statistiques.js:404:179"), v.entropie_relative], [tj("engine/static/engine/js/statistiques.js:404:223"), v.gini_simpson], [tj("engine/static/engine/js/statistiques.js:404:270"), v.denombrement.uniques], [tj("engine/static/engine/js/statistiques.js:404:327"), v.denombrement.rares], [tj("engine/static/engine/js/statistiques.js:404:378"), fmtPct(v.denombrement.part_rares)],
    ...(v.mediane !== undefined ? [[tj("engine/static/engine/js/statistiques.js:405:38"), v.mediane], ["1er quartile", v.q1], ["3e quartile", v.q3]] : [])]));
  const l = liste(v.lecture); if (l) c.append(l);
  const ordo = v.genre === "quali_ordinale";
  const mods = v.tableau.map(t => t.modalite), effs = v.tableau.map(t => t.effectif), sansAutres = v.tableau.every(t => t.cumul != null);
  c.append(el("div", {class: "sx-graphe"}, barres(mods, effs, v.tableau.length > 8 && !ordo),
    ...(v.tableau.length > 1 ? [el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:410:64")), secteurs(mods.length > 12 ? [...mods.slice(0, 11), tj("engine/static/engine/js/statistiques.js:lbl_autres", {v1: mods.length - 11})] : mods, mods.length > 12 ? [...effs.slice(0, 11), effs.slice(11).reduce((a, b) => a + b, 0)] : effs)] : []),
    ...(!ordo && sansAutres && v.tableau.length > 2 ? [el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:411:87")), pareto(mods, effs, v.tableau.map(t => t.cumul))] : []),
    tableau([tj("engine/static/engine/js/statistiques.js:412:15"), tj("engine/static/engine/js/statistiques.js:lbl_effectif"), tj("engine/static/engine/js/statistiques.js:412:39"), "", tj("engine/static/engine/js/statistiques.js:383:135")], (pb => v.tableau.map((t, i) => [t.modalite, t.effectif, fmtPct(t.frequence), barreFrequence(t.frequence, pb[i]), t.cumul == null ? null : fmtPct(t.cumul)]))(palette(v.tableau.length)))));
  if (v.par) c.append(parGroupe(v.par));
  return c;
}
function parGroupe(p) {
  if (p.erreur) return el("div", {class: "sx-erreur"}, p.erreur);
  const b = el("div", {class: "sx-graphe"}, el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:selon_variable", {v1: p.variable_groupe})));
  if (p.quanti) {
    b.append(tableau([tj("engine/static/engine/js/statistiques.js:lbl_groupe"), "n", tj("engine/static/engine/js/statistiques.js:420:39"), tj("engine/static/engine/js/statistiques.js:420:50"), tj("engine/static/engine/js/statistiques.js:421:147"), "Min", "Max"], p.quanti.map(g => [g.groupe, g.n, g.moyenne, g.mediane, g.ecart_type, g.min, g.max])));
    for (const g of p.quanti) if (g.boite) b.append(el("div", {class: "sx-note"}, g.groupe), boite(g.boite));
  } else if (p.croise) {
    b.append(tableau([tj("engine/static/engine/js/statistiques.js:lbl_groupe"), tj("engine/static/engine/js/statistiques.js:424:34"), ...p.modalites], p.croise.map(g => [g.groupe, g.total, ...g.effectifs.map(e => `${e} (${g.total ? fmt(Math.round(e / g.total * 1000) / 10) : 0} %)`)])));
  }
  return b;
}
function carteSynthese(s) {   // constats en langage clair (calculés côté serveur à partir des descriptives) + qualité des données
  const c = carte(tj("engine/static/engine/js/statistiques.js:428:20"));
  c.append(s.constats.length ? el("ul", {class: "sx-lecture sx-synthese"}, ...s.constats.map(k => el("li", {class: k.niveau === "attention" ? "sx-attention" : ""}, k.texte))) : el("div", {class: "sx-info"}, tj("engine/static/engine/js/statistiques.js:429:209")));
  if (s.qualite.length) c.append(el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:430:65")), tableau([tj("engine/static/engine/js/statistiques.js:430:98"), tj("engine/static/engine/js/statistiques.js:430:109"), "Manquantes"], s.qualite.map(q => [q.nom, q.n, q.manquants ? `${q.manquants} (${fmtPct(q.taux_manquants)})` : "0"])));
  return c;
}
function grapheLien(g) {   // graphique propre à la mesure du lien : nuage + droite (Pearson), composition des lignes (V de Cramér), boîtes par groupe (η²)
  if (g.type === "nuage") return nuage(g, g.nom_x, g.nom_y, g.droite);
  if (g.type === "croise") return el("div", {}, empilees(g.lignes, g.colonnes, g.effectifs), tableau(["", ...g.colonnes], g.lignes.map((l, i) => [l, ...g.effectifs[i]])));
  const d = el("div", {});
  for (const q of g.groupes) d.append(el("div", {class: "sx-note"}, `${q.nom} (n = ${q.n})`), boite(q.boite));
  return d;
}
function matriceCorr(m, titre) {
  const t = el("table", {class: tj("engine/static/engine/js/statistiques.js:441:34")});
  t.append(el("thead", {}, el("tr", {}, el("th", {}), ...m.noms.map(n => el("th", {}, n)))));
  const tb = el("tbody", {});
  (m[titre] || []).forEach((lig, i) => tb.append(el("tr", {}, el("th", {}, m.noms[i]), ...lig.map(v => {
    const td = el("td", {}, v == null ? "—" : v.toFixed(2).replace(".", ","));
    if (v != null) td.style.background = v >= 0 ? gyCouleur("--ok", "#2ecc71", Math.abs(v) * .55) : gyCouleur("--danger", "#e74c3c", Math.abs(v) * .55);
    return td;
  }))));
  t.append(tb);
  return el("div", {class: "sx-defil"}, t);
}
async function lancerDescriptives() {
  const zone = $("d-res"), cols = colonnesCochees();
  if (!cols.length) { toast(tj("engine/static/engine/js/statistiques.js:454:30"), true); return; }
  chargement(zone); _curseur = 0;
  try {
    const d = await api("descriptives", {...parametresCommuns(), colonnes: cols.join(","), par: $("d-par").value});
    zone.textContent = "";
    zone.append(...resumeMemoire(d));
    if (d.synthese) zone.append(carteSynthese(d.synthese));
    for (const v of d.variables) zone.append(v.genre && v.genre.startsWith("quanti") ? carteQuanti(v) : (v.genre === "date" ? carteDate(v) : v.genre ? carteQuali(v) : carte(v.ref, el("div", {class: "sx-erreur"}, v.erreur || ""))));
    for (const r of d.relations || []) {
      const c = carte(tj("engine/static/engine/js/statistiques.js:liens_entre_variables", {v1: r.table}));
      if (r.paires.length) c.append(tableau([tj("engine/static/engine/js/statistiques.js:lbl_var1"), tj("engine/static/engine/js/statistiques.js:lbl_var2"), tj("engine/static/engine/js/statistiques.js:lbl_mesure"), tj("engine/static/engine/js/statistiques.js:464:85"), "p", "n", tj("engine/static/engine/js/statistiques.js:lbl_force")], r.paires.map(p => [p.a, p.b, p.mesure, p.valeur, fmtP(p.p), p.n, p.force])));
      const avecGraphe = r.paires.filter(p => p.graphique);
      if (avecGraphe.length) c.append(el("details", {open: "open"}, el("summary", {class: "sx-note"}, tj("engine/static/engine/js/statistiques.js:466:104", {v1: avecGraphe.length})),
        ...avecGraphe.map(p => el("div", {class: "sx-graphe"}, el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:lien_mesure", {v1: p.a, v2: p.b, v3: p.mesure, v4: fmt(Math.round(p.valeur * 1000) / 1000), v5: p.force})), grapheLien(p.graphique)))));
      if (r.matrice) c.append(el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:468:62")), matriceCorr(r.matrice, "pearson"), el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:468:155")), matriceCorr(r.matrice, "spearman"));
      zone.append(c);
    }
  } catch (e) { erreur(zone, e); }
}
function resumeMemoire(d) {
  const sortie = [];
  if (d.echantillon) sortie.push(el("div", {class: "sx-info"}, tj("engine/static/engine/js/statistiques.js:475:65", {v1: d.echantillon.methode, v2: d.echantillon.taille, v3: d.echantillon.remise ? tj("engine/static/engine/js/statistiques.js:475:161") : "", v4: d.echantillon.graine == null ? "—" : d.echantillon.graine})));
  (d.tables_de_calcul || []).forEach(t => { if (d.tables_de_calcul.length > 1 || t.lignes !== t.lignes_avant) sortie.push(el("div", {class: "sx-info"}, tj("engine/static/engine/js/statistiques.js:476:154", {v1: t.tables.join(" + "), v2: t.lignes}) + (t.lignes !== t.lignes_avant ? tj("engine/static/engine/js/statistiques.js:sur_n_avant", {v1: t.lignes_avant}) : "") + ".")); });
  if (d.homogene === false) sortie.push(el("div", {class: "sx-info"}, tj("engine/static/engine/js/statistiques.js:477:72")));
  for (const a of d.avertissements || []) sortie.push(el("div", {class: "sx-avert"}, a));
  if ((d.refusees || []).length) sortie.push(el("div", {class: "sx-avert"}, tj("engine/static/engine/js/statistiques.js:479:78") + d.refusees.join(", ")));
  return sortie;
}

// ── Statistiques inférentielles ──────────────────────────────────────────────
function dessinerListeTests() {
  const sel = $("i-test"), familles = {};
  for (const t of S.tests) (familles[t.famille] = familles[t.famille] || []).push(t);
  for (const [f, ts] of Object.entries(familles)) sel.append(el("optgroup", {label: f}, ...ts.map(t => el("option", {value: t.cle}, t.nom))));
  sel.onchange = majTest;
  majTest();
}
function majTest() {
  S.test = S.tests.find(t => t.cle === $("i-test").value);
  $("i-aide").textContent = S.test.aide || "";
  S.roles = S.test.roles;
  majRoles();
  const zp = $("i-params"); zp.textContent = "";
  for (const p of S.test.params) {
    const champ = p.type === "select" ? el("select", {id: "ip-" + p.cle}, ...p.options.map(o => el("option", {value: o[0]}, o[1])))
      : el("input", {id: "ip-" + p.cle, type: p.type === "number" ? "number" : "text", step: "any"});
    champ.value = p.defaut;
    zp.append(el("label", {class: "geo-champ"}, p.libelle, champ));
  }
  $("i-lancer").style.display = "";
}
function majRoles() {
  const zr = $("i-roles"), anciens = {};
  zr.querySelectorAll("select[data-role]").forEach(s => { anciens[s.getAttribute("data-role")] = s.multiple ? Array.from(s.selectedOptions).map(o => o.value) : s.value; });
  zr.textContent = "";
  const cols = toutesColonnes();
  for (const r of S.roles) {
    const filtre = c => r.genre === "quanti" ? c.famille === "quanti" : r.genre === "quali" ? c.famille !== "quanti" : true;
    const sel = el("select", {"data-role": r.cle});
    if (r.multiple) { sel.multiple = true; sel.size = 6; } else sel.append(el("option", {value: ""}, r.facultatif ? tj("engine/static/engine/js/statistiques.js:513:118") : tj("engine/static/engine/js/statistiques.js:513:129")));
    for (const c of cols.filter(filtre)) sel.append(el("option", {value: c.ref}, c.nom));
    const ancien = anciens[r.cle];
    if (ancien) { if (r.multiple) Array.from(sel.options).forEach(o => { o.selected = ancien.includes(o.value); }); else sel.value = ancien; }
    zr.append(el("label", {class: "geo-champ"}, r.libelle + (r.multiple ? tj("engine/static/engine/js/statistiques.js:517:76") : ""), sel));
  }
}
async function lancerTest() {
  const zone = $("i-res"), p = {...parametresCommuns(), test: S.test.cle};
  for (const s of $("i-roles").querySelectorAll("select[data-role]")) {
    const r = s.getAttribute("data-role");
    p[r] = s.multiple ? Array.from(s.selectedOptions).map(o => o.value).join(",") : s.value;
  }
  for (const pr of S.test.params) p[pr.cle] = $("ip-" + pr.cle).value;
  chargement(zone); _curseur = 0;
  try { afficherTest(zone, await api("test", p)); } catch (e) { erreur(zone, e); }
}
function afficherTest(zone, r) {
  zone.textContent = "";
  zone.append(...resumeMemoire(r));
  const c = carte(r.test);
  if (r.choix_auto) c.append(el("div", {class: "sx-info"}, tj("engine/static/engine/js/statistiques.js:535:61") + r.choix_auto.nom + ". " + r.choix_auto.raison));
  if (r.variables && r.variables.length) c.append(el("div", {class: "sx-note"}, r.variables.map(v => `${v.nom} (${v.genre}, n = ${v.n})`).join(" · ")));
  if (r.h0) c.append(paires([[tj("engine/static/engine/js/statistiques.js:536:32"), r.h0], [tj("engine/static/engine/js/statistiques.js:536:62"), r.h1]]));
  const kv = [];
  if (r.statistique) kv.push([r.statistique.nom, r.statistique.valeur]);
  if (r.ddl != null) kv.push([tj("engine/static/engine/js/statistiques.js:539:32"), r.ddl]);
  if (r.p != null) kv.push(["p-valeur", fmtP(r.p)], ["Seuil α", fmt(r.alpha)]);
  if (r.effet) kv.push([r.effet.nom, r.effet.valeur]);
  c.append(paires(kv));
  c.append(el("div", {class: "sx-conclusion " + (r.significatif === true ? "sig" : r.significatif === false ? "nonsig" : "")}, r.conclusion));
  if (r.intervalle) c.append(el("div", {class: "sx-note"}, tj("engine/static/engine/js/statistiques.js:intervalle", {v1: r.intervalle.libelle, v2: fmt(r.intervalle.bas), v3: fmt(r.intervalle.haut)})));
  for (const a of r.avertissements || []) c.append(el("div", {class: "sx-avert"}, a));
  if (r.estimation) c.append(paires(Object.entries(r.estimation).map(([k, v]) => [k.replace(/_/g, " "), v])));
  if (r.groupes) c.append(el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:lbl_groupes")), r.groupes[0] && r.groupes[0].proportion !== undefined
    ? tableau([tj("engine/static/engine/js/statistiques.js:lbl_groupe"), "n", tj("engine/static/engine/js/statistiques.js:548:32"), "Proportion"], r.groupes.map(g => [g.nom, g.n, g.succes, fmtPct(g.proportion * 100)]))
    : tableau([tj("engine/static/engine/js/statistiques.js:lbl_groupe"), "n", tj("engine/static/engine/js/statistiques.js:549:32"), tj("engine/static/engine/js/statistiques.js:550:86"), tj("engine/static/engine/js/statistiques.js:549:57"), "Min", "Max"], r.groupes.map(g => [g.nom, g.n, g.moyenne, g.ecart_type, g.mediane, g.min, g.max])));
  if (r.groupes && r.groupes.length) {
    const prop = r.groupes[0].proportion !== undefined;
    c.append(el("div", {class: "sx-graphe"}, el("div", {class: "sx-sous"}, prop ? tj("engine/static/engine/js/statistiques.js:552:84") : tj("engine/static/engine/js/statistiques.js:552:120")),
      prop ? barres(r.groupes.map(g => g.nom), r.groupes.map(g => Math.round(g.proportion * 1000) / 10)) : intervalles(r.groupes)));
  }
  if (r.tableau_anova) c.append(el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:555:64")), tableau(["Source", tj("engine/static/engine/js/statistiques.js:555:117"), "ddl", tj("engine/static/engine/js/statistiques.js:555:144"), "F", "p"], r.tableau_anova.map(l => [l.source, l.ss, l.ddl, l.ms, l.f, l.p == null ? null : fmtP(l.p)])));
  if (r.comparaisons && r.comparaisons.lignes.length) c.append(el("div", {class: "sx-sous"}, r.comparaisons.libelle), tableau([tj("engine/static/engine/js/statistiques.js:groupe_a"), tj("engine/static/engine/js/statistiques.js:groupe_b"), tj("engine/static/engine/js/statistiques.js:556:153"), tj("engine/static/engine/js/statistiques.js:556:179"), tj("engine/static/engine/js/statistiques.js:significatif")], r.comparaisons.lignes.map(l => [l.a, l.b, l.difference, fmtP(l.p), l.significatif ? tj("engine/static/engine/js/statistiques.js:oui") : tj("engine/static/engine/js/statistiques.js:non")])));
  if (r.contingence) {
    const t = r.contingence;
    c.append(el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:559:45") + (t.attendus ? tj("engine/static/engine/js/statistiques.js:559:82") : "")),
      tableau(["", ...t.colonnes], t.lignes.map((l, i) => [l, ...t.observes[i].map((o, j) => t.attendus ? `${fmt(o)} (${fmt(Math.round(t.attendus[i][j] * 100) / 100)})` : fmt(o))])));
    if (t.residus) c.append(el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:561:60")), tableau(["", ...t.colonnes], t.lignes.map((l, i) => [l, ...t.residus[i].map(v => v.toFixed(2).replace(".", ","))])));
  }
  if (r.contingence) c.append(el("div", {class: "sx-graphe"}, el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:563:94")), empilees(r.contingence.lignes, r.contingence.colonnes, r.contingence.observes)));
  if (r.tableau && r.tableau.length && r.tableau[0].attendu !== undefined) c.append(el("div", {class: "sx-graphe"}, el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:564:148")), observeAttendu(r.tableau)));
  if (r.tableau) c.append(el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:565:58")), tableau([tj("engine/static/engine/js/statistiques.js:565:90"), tj("engine/static/engine/js/statistiques.js:565:102"), tj("engine/static/engine/js/statistiques.js:lbl_attendu"), tj("engine/static/engine/js/statistiques.js:565:124")], r.tableau.map(l => [l.modalite, l.observe, l.attendu, l.residu])));
  if (r.coefficients) {
    c.append(el("div", {class: "sx-sous"}, r.equation), (r.coefficients.some(k => k.ou != null)
      ? tableau([tj("engine/static/engine/js/statistiques.js:lbl_terme"), "Coefficient", tj("engine/static/engine/js/statistiques.js:568:43"), "z", "p", tj("engine/static/engine/js/statistiques.js:lbl_ic_bas"), tj("engine/static/engine/js/statistiques.js:lbl_ic_haut"), "Odds ratio", tj("engine/static/engine/js/statistiques.js:lbl_or_bas"), tj("engine/static/engine/js/statistiques.js:lbl_or_haut")], r.coefficients.map(k => [k.nom, k.coef, k.se, k.t, fmtP(k.p), k.bas, k.haut, k.ou, k.ou_bas, k.ou_haut]))
      : tableau([tj("engine/static/engine/js/statistiques.js:lbl_terme"), "Coefficient", tj("engine/static/engine/js/statistiques.js:569:43"), "t", "p", tj("engine/static/engine/js/statistiques.js:lbl_ic_bas"), tj("engine/static/engine/js/statistiques.js:lbl_ic_haut")], r.coefficients.map(k => [k.nom, k.coef, k.se, k.t, fmtP(k.p), k.bas, k.haut]))));
  }
  if (r.nuage) c.append(el("div", {class: "sx-graphe"}, nuage(r.nuage, r.nuage.nom_x, r.nuage.nom_y, r.estimation && r.estimation.pente != null && r.estimation.ordonnee != null ? r.estimation : null)));
  if (r.coupes) r.coupes.forEach(q => c.append(el("div", {class: "sx-graphe"}, el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:572:111") + q.nom_x + tj("engine/static/engine/js/statistiques.js:572:137")), nuage(q, q.nom_x, q.nom_y, null))));
  if (r.cartes) {
    const bloc = q => el("div", {class: "sx-graphe"}, el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:x_et_y", {v1: q.nom_x, v2: q.nom_y}) + tj("engine/static/engine/js/statistiques.js:574:126")), carteChaleur(q),
      el("details", {class: "sx-note"}, el("summary", {}, tj("engine/static/engine/js/statistiques.js:575:60")), el("div", {class: "sx-3d"})));
    const rendre = q => { const b = bloc(q), d = b.querySelector("details"); d.addEventListener("toggle", () => { const z = d.querySelector(".sx-3d"); if (d.open && !z.firstChild) z.append(surface3D(q)); }); return b; };
    r.cartes.slice(0, 6).forEach(q => c.append(rendre(q)));
    if (r.cartes.length > 6) {   // les autres couples, moins marqués : repliés et dessinés à l'ouverture seulement
      const reste = el("details", {class: "sx-note"}, el("summary", {}, (r.cartes.length - 6) + tj("engine/static/engine/js/statistiques.js:579:98")));
      reste.addEventListener("toggle", () => { if (reste.open && reste.children.length === 1) r.cartes.slice(6).forEach(q => reste.append(rendre(q))); });
      c.append(reste);
    }
  }
  if (r.qq) c.append(el("div", {class: "sx-graphe"}, qq(r.qq)));
  if (r.histogramme) c.append(el("div", {class: "sx-graphe"}, el("div", {class: "sx-sous"}, tj("engine/static/engine/js/statistiques.js:585:94")), histogramme(r.histogramme)));
  zone.append(c);
}

// ── Probabilités ─────────────────────────────────────────────────────────────
function champNombre(id, libelle, defaut, extra) { return el("label", {class: "geo-champ"}, libelle, el("input", {id, type: "number", step: "any", value: defaut == null ? "" : defaut, ...(extra || {})})); }
function majProbabilites() {
  const t = $("p-type").value, f = $("p-formulaire");
  f.textContent = "";
  if (t === "loi") {
    const loi = el("select", {id: "pl-loi"});
    const g = (nom, discr) => el("optgroup", {label: nom}, ...S.lois.filter(l => l.discrete === discr).map(l => el("option", {value: l.cle}, l.nom)));
    loi.append(g(tj("engine/static/engine/js/statistiques.js:lbl_lois_continues"), false), g(tj("engine/static/engine/js/statistiques.js:597:47"), true));
    loi.onchange = majParamsLoi;
    f.append(el("label", {class: "geo-champ", style: "min-width:min(300px,100%)"}, "Loi", loi), el("div", {id: "pl-params", class: "sx-formulaire"}),
      champNombre("pl-x", tj("engine/static/engine/js/statistiques.js:valeur_x"), ""), champNombre("pl-bas", tj("engine/static/engine/js/statistiques.js:entre_a"), ""), champNombre("pl-haut", tj("engine/static/engine/js/statistiques.js:600:140"), ""),
      champNombre("pl-q", tj("engine/static/engine/js/statistiques.js:601:28"), ""), champNombre("pl-niveau", tj("engine/static/engine/js/statistiques.js:601:92"), ""));
    majParamsLoi();
  } else if (t === "evenements") {
    f.append(champNombre("pe-pa", "P(A)", 0.5, {min: 0, max: 1}), champNombre("pe-pb", "P(B)", 0.4, {min: 0, max: 1}),
      el("label", {class: "geo-champ", style: "min-width:260px"}, tj("engine/static/engine/js/statistiques.js:605:68"), el("select", {id: "pe-rel"}, ...Object.entries(CAT.relations || {}).map(([k, n]) => el("option", {value: k}, n)))),
      champNombre("pe-val", tj("engine/static/engine/js/statistiques.js:606:30"), ""));
  } else if (t === "bayes") {
    f.append(el("div", {class: "geo-note", style: "width:100%"}, tj("engine/static/engine/js/statistiques.js:608:67")), el("div", {id: "pb-lignes", style: "width:100%"}),
      el("button", {class: "geo-btn", style: "max-width:200px", onclick: () => ligneBayes("", "", "")}, tj("engine/static/engine/js/statistiques.js:609:106")));
    ligneBayes(tj("engine/static/engine/js/statistiques.js:lbl_malade"), 0.01, 0.99); ligneBayes(tj("engine/static/engine/js/statistiques.js:lbl_sain"), 0.99, 0.05);
  } else if (t === "combinatoire") {
    f.append(champNombre("pc-n", tj("engine/static/engine/js/statistiques.js:612:35"), 10, {min: 0, max: 5000}), champNombre("pc-k", "k (choisis)", 3, {min: 0, max: 5000}));
  } else {
    f.append(el("label", {class: "geo-champ", style: "min-width:min(300px,100%)"}, tj("engine/static/engine/js/statistiques.js:614:75"), el("select", {id: "pa-col"}, ...toutesColonnes().filter(c => c.famille === "quanti").map(c => el("option", {value: c.ref}, c.nom)))),
      champNombre("pa-x", tj("engine/static/engine/js/statistiques.js:615:28"), ""));
  }
  $("p-res").textContent = "";
}
function ligneBayes(nom, prior, vrais) {
  const l = el("div", {class: "sx-ligne-bayes"}, el("input", {type: "text", placeholder: tj("engine/static/engine/js/statistiques.js:620:91"), value: nom, "data-b": "nom"}), el("input", {type: "number", step: "any", min: 0, max: 1, placeholder: tj("engine/static/engine/js/statistiques.js:lbl_a_priori"), value: prior, "data-b": "prior"}),
    el("input", {type: "number", step: "any", min: 0, max: 1, placeholder: "P(obs | hyp.)", value: vrais, "data-b": "vraisemblance"}), el("button", {class: "geo-btn", style: "width:auto", onclick: () => l.remove(), title: tj("engine/static/engine/js/statistiques.js:lbl_retirer")}, "✕"));
  $("pb-lignes").append(l);
}
function majParamsLoi() {
  const loi = S.lois.find(l => l.cle === $("pl-loi").value), z = $("pl-params");
  z.textContent = "";
  for (const p of loi.params) z.append(champNombre("plp-" + p.cle, p.libelle, p.defaut, p.entier ? {step: 1} : {}));
}
async function lancerProbabilites() {
  const t = $("p-type").value, zone = $("p-res"), p = {type: t};
  if (t === "loi") {
    p.loi = $("pl-loi").value;
    for (const l of S.lois.find(l => l.cle === p.loi).params) p["p_" + l.cle] = $("plp-" + l.cle).value;
    Object.assign(p, {x: $("pl-x").value, bas: $("pl-bas").value, haut: $("pl-haut").value, q: $("pl-q").value, niveau: $("pl-niveau").value});
    if (!p.x && !(p.bas && p.haut) && !p.q && !p.niveau) { toast(tj("engine/static/engine/js/statistiques.js:635:67"), true); return; }
  } else if (t === "evenements") Object.assign(p, {pa: $("pe-pa").value, pb: $("pe-pb").value, relation: $("pe-rel").value, valeur: $("pe-val").value});
  else if (t === "bayes") p.hypotheses = JSON.stringify(Array.from($("pb-lignes").children).map(l => Object.fromEntries(Array.from(l.querySelectorAll("input")).map(i => [i.getAttribute("data-b"), i.value]))));
  else if (t === "combinatoire") Object.assign(p, {n: $("pc-n").value, k: $("pc-k").value});
  else Object.assign(p, parametresCommuns(), {colonne: $("pa-col") && $("pa-col").value, x: $("pa-x").value});
  chargement(zone); _curseur = 0;
  try { afficherProbabilites(zone, t, await api("probabilites", p)); } catch (e) { erreur(zone, e); }
}
function afficherProbabilites(zone, t, r) {
  zone.textContent = "";
  const lignes = rs => tableau([tj("engine/static/engine/js/statistiques.js:645:34"), tj("engine/static/engine/js/statistiques.js:645:49")], rs.map(x => [x.libelle, x.valeur == null ? "—" : (x.valeur >= 0 && x.valeur <= 1 && !/Quantile|borne/.test(x.libelle) && !/^f\(/.test(x.libelle) ? `${fmt(Math.round(x.valeur * 1e8) / 1e8)}  (${fmt(Math.round(x.valeur * 1e6) / 1e4)} %)` : fmt(x.valeur))]));
  if (t === "loi") {
    const c = carte(r.nom);
    c.append(paires([...Object.entries(r.params).map(([k, v]) => [k, v]), [tj("engine/static/engine/js/statistiques.js:648:77"), r.moyenne], [tj("engine/static/engine/js/statistiques.js:648:103"), r.variance], [tj("engine/static/engine/js/statistiques.js:649:213"), r.ecart_type]]));
    for (const a of r.avertissements || []) c.append(el("div", {class: "sx-avert"}, a));
    c.append(lignes(r.resultats), el("div", {class: "sx-graphe"}, el("div", {class: "sx-sous"}, r.discrete ? tj("engine/static/engine/js/statistiques.js:650:111") : tj("engine/static/engine/js/statistiques.js:650:164")), courbeLoi(r.courbe, r.marques)));
    zone.append(c);
  } else if (t === "evenements") {
    zone.append(carte(tj("engine/static/engine/js/statistiques.js:653:24"), lignes(r.resultats), el("div", {class: "sx-note"}, (r.independants ? tj("engine/static/engine/js/statistiques.js:653:114") : tj("engine/static/engine/js/statistiques.js:653:145")) + (r.incompatibles ? tj("engine/static/engine/js/statistiques.js:653:203") : ""))));
  } else if (t === "bayes") {
    zone.append(carte(tj("engine/static/engine/js/statistiques.js:655:24"), el("div", {class: "sx-note"}, tj("engine/static/engine/js/statistiques.js:655:74") + fmt(r.total)),
      tableau([tj("engine/static/engine/js/statistiques.js:656:17"), "A priori", tj("engine/static/engine/js/statistiques.js:lbl_vraisemblance"), tj("engine/static/engine/js/statistiques.js:lbl_conjointe"), "A posteriori", ""], (pb => r.lignes.map((l, i) => [l.nom, l.prior, l.vraisemblance, l.conjointe, fmtPct(l.posterieure * 100), barreFrequence(l.posterieure * 100, pb[i])]))(palette(r.lignes.length)))));
  } else if (t === "combinatoire") {
    zone.append(carte(tj("engine/static/engine/js/statistiques.js:658:24"), tableau([tj("engine/static/engine/js/statistiques.js:lbl_formule"), tj("engine/static/engine/js/statistiques.js:658:60"), tj("engine/static/engine/js/statistiques.js:658:77")], r.resultats.map(x => [x.libelle, x.exact, x.valeur]))));
  } else {
    const c = carte(tj("engine/static/engine/js/statistiques.js:660:22") + r.nom + " »", ...resumeMemoire(r), paires([["n", r.n], [tj("engine/static/engine/js/statistiques.js:660:101"), r.moyenne], [tj("engine/static/engine/js/statistiques.js:661:201"), r.ecart_type]]));
    c.append(tableau(["Loi", tj("engine/static/engine/js/statistiques.js:661:31"), tj("engine/static/engine/js/statistiques.js:661:53"), "p (indicative)", "AIC"], r.ajustements.map((a, i) => [(i === 0 ? "★ " : "") + a.nom, Object.entries(a.params).map(([k, v]) => k + " = " + fmt(v)).join(" ; "), a.ks_d, fmtP(a.ks_p), a.aic])),
      el("div", {class: "sx-note"}, tj("engine/static/engine/js/statistiques.js:662:38")));
    if (r.empirique) c.append(el("div", {class: "sx-note"}, tj("engine/static/engine/js/statistiques.js:663:62", {v1: fmt(r.empirique.x), v2: fmtPct(r.empirique.p_inf * 100), v3: fmt(r.empirique.x), v4: fmtPct(r.empirique.p_sup * 100)})));
    zone.append(c);
  }
}

// ── Onglets de la page et démarrage ──────────────────────────────────────────
function montrer(partie) {
  ctx.noter({partie});
  document.querySelectorAll(".sx-onglets .onglet-vue").forEach(b => b.classList.toggle("actif", b.getAttribute("data-partie") === partie));
  for (const p of ["desc", "inf", "prob"]) $("sx-" + p).style.display = p === partie ? "" : "none";
  if (partie === "prob" && $("p-type").value === "ajustement") majProbabilites();
}
document.querySelectorAll(".sx-onglets .onglet-vue").forEach(b => { b.onclick = () => montrer(b.getAttribute("data-partie")); });
$("d-lancer").onclick = () => { ctx.noter({dernier: "desc"}); return lancerDescriptives(); };
$("i-lancer").onclick = () => { ctx.noter({dernier: "inf"}); return lancerTest(); };
$("p-lancer").onclick = () => { ctx.noter({dernier: "prob"}); return lancerProbabilites(); };
$("p-type").onchange = majProbabilites;
initEchantillon();
dessinerTables();
dessinerListeTests();
majProbabilites();
// Retour sur la page : onglet, tables et colonnes cochées, test choisi, paramètres remis ; la dernière analyse est relancée.
const sauve = ctx.restaurer();
if (sauve.partie) montrer(sauve.partie);
if (sauve.dernier === "desc") lancerDescriptives();
else if (sauve.dernier === "inf") lancerTest();
else if (sauve.dernier === "prob") lancerProbabilites();
if (!C.tableCompatible) $("d-res").append(el("div", {class: "sx-vide"}, tj("engine/static/engine/js/statistiques.js:690:74")));
})();
