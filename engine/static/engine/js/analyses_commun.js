// ANOVA et ANALYSE FACTORIELLE : briques communes (sélection des tables / colonnes, appel API, tableaux, graphiques SVG sans dépendance).
// Aucun calcul ici : tout vient du serveur (engine/analyses_auto.py) ; la page choisit les colonnes, lance et affiche.
(function () {
"use strict";
const C = window.ANALYSE;
const TABLES = JSON.parse(document.getElementById("sx-donnees-tables").textContent || "[]");
const CAT = JSON.parse(document.getElementById("sx-donnees-catalogue").textContent || "{}");
const GENRES = CAT.genres || {};
GYContexte.init({page: C.page, tableId: C.tableId});
const G = window.GYAnalyse = {C, CAT, genres: {}};

const $ = G.$ = id => document.getElementById(id);
const el = G.el = function (tag, attrs, ...enfants) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) { if (k === "class") n.className = v; else if (k.startsWith("on")) n[k] = v; else n.setAttribute(k, v); }
  for (const e of enfants) if (e != null) n.append(e instanceof Node ? e : document.createTextNode(String(e)));
  return n;
};
const sv = G.sv = function (tag, attrs, ...enfants) {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
  for (const e of enfants) if (e != null) n.append(e instanceof Node ? e : document.createTextNode(String(e)));
  return n;
};
G.toast = function (msg, err) { const t = $("toast"); t.textContent = msg; t.className = err ? "err" : ""; t.style.display = "block"; clearTimeout(t._t); t._t = setTimeout(() => { t.style.display = "none"; }, 4500); };
const nf = new Intl.NumberFormat((window.I18N_LOCALE||"fr-FR"), {maximumSignificantDigits: 4});
const fmt = G.fmt = v => (v == null || (typeof v === "number" && !isFinite(v))) ? "—" : (typeof v === "number" ? nf.format(v) : String(v));
G.fmtP = p => p == null ? "—" : (p < 1e-4 ? "< 0,0001" : p.toFixed(4).replace(".", ","));
G.fmtPct = v => v == null ? "—" : nf.format(Math.round(v * 10) / 10) + " %";

G.api = async function (chemin, params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) if (v !== null && v !== undefined && v !== "") q.set(k, v);
  const res = await fetch(`/api/tables/${C.tableId}/${chemin}/?` + q.toString(), {headers: {"Accept": "application/json"}});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.erreur || (tj("engine/static/engine/js/analyses_commun.js:36:49") + res.status));
  return data;
};

// ── Sélection : tables et colonnes (le type est reconnu automatiquement ; corrigeable colonne par colonne) ──
function dessinerTables() {
  const zone = $("sx-tables");
  zone.textContent = "";
  if (!TABLES.length) { zone.append(el("div", {class: "geo-note"}, tj("engine/static/engine/js/analyses_commun.js:44:69"))); return; }
  for (const t of TABLES) {
    const courante = t.id === C.tableId;
    const entete = el("label", {class: "geo-case"},
      el("input", {type: "checkbox", "data-tout": t.id, onchange: e => { zone.querySelectorAll(`input[data-table="${t.id}"]`).forEach(i => { i.checked = e.target.checked; }); majMemoire(); }}),
      el("b", {}, " " + t.nom + (courante ? " (ouverte)" : "")));
    const bloc = el("div", {class: "st-table"}, entete);
    for (const c of t.colonnes) {
      const k = t.id + ":" + c.slug;
      const sel = el("select", {class: "sx-type", title: tj("engine/static/engine/js/analyses_commun.js:53:59"), onchange: e => { if (e.target.value) G.genres[k] = e.target.value; else delete G.genres[k]; }},
        el("option", {value: ""}, "auto"), ...Object.entries(GENRES).map(([g, n]) => el("option", {value: g}, n)));
      bloc.append(el("label", {class: "geo-case st-col"}, el("input", {type: "checkbox", "data-table": t.id, "data-slug": c.slug, onchange: majMemoire}), " " + c.nom, sel));
    }
    zone.append(bloc);
  }
  if (window.GYFiltreTables) GYFiltreTables.installer(zone, {courant: C.tableId});   // une seule table affichée à la fois (filtre)
  majMemoire();
}
G.colonnes = () => Array.from(document.querySelectorAll("#sx-tables input[data-table]:checked")).map(i => i.getAttribute("data-table") + ":" + i.getAttribute("data-slug"));
G.tables = () => { const ids = new Set(G.colonnes().map(r => Number(r.split(":")[0]))); ids.add(C.tableId); return Array.from(ids); };
function majMemoire() {
  const sig = new Set(TABLES.filter(t => G.tables().includes(t.id)).map(t => t.signature));
  $("sx-memoire").textContent = G.tables().length < 2 ? tj("engine/static/engine/js/analyses_commun.js:65:58")
    : (sig.size <= 1 ? tj("engine/static/engine/js/analyses_commun.js:66:25") : tj("engine/static/engine/js/analyses_commun.js:66:113"));
}
G.parametres = function (extra) {
  const types = Object.entries(G.genres).map(([k, g]) => k + ":" + g).join(",");
  const autres = G.tables().filter(id => id !== C.tableId).join(",");
  return Object.assign({colonnes: G.colonnes().join(","), types, tables: autres}, extra || {});
};

// ── Affichage ────────────────────────────────────────────────────────────────
G.carte = (titre, ...contenu) => el("div", {class: "sx-carte"}, el("div", {class: "st-titre"}, titre), ...contenu);
G.sous = t => el("div", {class: "sx-sous"}, t);
G.liste = (items, classe) => items && items.length ? el("ul", {class: classe || "sx-lecture"}, ...items.map(i => el("li", {}, i))) : null;
G.tableau = function (entetes, lignes) {
  const t = el("table", {class: "sx-table"}, el("thead", {}, el("tr", {}, ...entetes.map(h => el("th", {}, h)))),
    el("tbody", {}, ...lignes.map(l => el("tr", {}, ...l.map(c => el("td", {}, c instanceof Node ? c : fmt(c)))))));
  return el("div", {class: "sx-defil"}, t);
};
G.avis = (liste) => (liste || []).map(a => el("div", {class: "sx-avert"}, a));
G.contexte = function (d) {   // tables de calcul, échantillon, colonnes écartées, tables refusées
  const sortie = [];
  (d.tables_de_calcul || []).forEach(t => { if (d.tables_de_calcul.length > 1 || t.lignes !== t.lignes_avant) sortie.push(el("div", {class: "sx-info"}, tj("engine/static/engine/js/analyses_commun.js:86:154", {v1: t.tables.join(" + "), v2: t.lignes}))); });
  for (const a of d.avertissements || []) sortie.push(el("div", {class: "sx-avert"}, a));
  for (const i of d.ignorees || []) sortie.push(el("div", {class: "sx-avert"}, tj("engine/static/engine/js/analyses_commun.js:88:81", {v1: i.colonne, v2: i.raison})));
  if ((d.refusees || []).length) sortie.push(el("div", {class: "sx-avert"}, tj("engine/static/engine/js/analyses_commun.js:89:78") + d.refusees.join(", ")));
  return sortie;
};
G.chargement = () => { const z = $("res"); z.textContent = ""; z.append(el("div", {class: "sx-vide"}, tj("engine/static/engine/js/analyses_commun.js:92:104"))); };
G.erreur = e => { const z = $("res"); z.textContent = ""; z.append(el("div", {class: "sx-erreur"}, e.message || String(e))); };
G.lancer = async function (chemin, params, dessiner) {
  if (!C.tableCompatible && !G.colonnes().length) { G.toast(tj("engine/static/engine/js/analyses_commun.js:95:62"), true); return; }
  G.chargement(); gyPaletteRaz();
  try { const d = await G.api(chemin, params); const z = $("res"); z.textContent = ""; z.append(...dessiner(d)); }
  catch (e) { G.erreur(e); }
};

// ── Graphiques SVG ───────────────────────────────────────────────────────────
G.couleur = i => gyCouleurN(i);   // couleur hexadécimale n° i (24 teintes, puis on repart du début)
// Légende : chaque couleur du graphique porte son libellé. entrees = [[couleur, libellé, forme]] (forme : "carre" | "ligne" | "point"), posée sous le tracé ; la hauteur du cadre grandit d'autant.
G.legende = function (svg, entrees) {
  const vb = svg.getAttribute("viewBox").split(" ").map(Number), W = vb[2], H = vb[3];
  let x = 8, y = H + 4, lignes = 1;
  const liste = entrees.length > 48 ? entrees.slice(0, 47).concat([["#9ca3af", "… +" + (entrees.length - 47), "carre"]]) : entrees;
  liste.forEach(([coul, lib, forme]) => {
    lib = String(lib);
    const w = 22 + lib.length * 5.6;
    if (x + w > W - 4 && x > 8) { x = 8; y += 14; lignes++; }
    svg.append(forme === "ligne" ? sv("line", {x1: x, x2: x + 12, y1: y + 5, y2: y + 5, stroke: coul, "stroke-width": 2})
      : forme === "point" ? sv("circle", {cx: x + 5, cy: y + 5, r: 3.4, fill: coul}) : sv("rect", {x, y, width: 10, height: 10, fill: coul, opacity: .9}),
      sv("text", {x: x + 15, y: y + 9, fill: gyCouleur("--texte-clair", "#d4d4d4"), "font-size": 10}, lib));
    x += w;
  });
  svg.setAttribute("viewBox", `0 0 ${W} ${H + 8 + lignes * 14}`);
  return svg;
};
function echelle(vals, marge) { let a = Math.min(...vals), b = Math.max(...vals); if (!isFinite(a)) { a = -1; b = 1; } if (a === b) { a -= 1; b += 1; } const m = (b - a) * (marge == null ? 0.08 : marge); return [a - m, b + m]; }
// Nuage de points avec axes en 0 (plan factoriel). pts = [{x, y, nom, couleur, rayon, gras}] ; fleches = tracé des vecteurs depuis l'origine (cercle des corrélations).
G.nuage = function (pts, opts) {
  opts = opts || {};
  const W = 560, H = 440, M = 36;
  const svg = sv("svg", {viewBox: `0 0 ${W} ${H}`, class: "sx-svg", role: "img"});
  const [x0, x1] = opts.xmin != null ? [opts.xmin, opts.xmax] : echelle(pts.map(p => p.x).concat([0]));
  const [y0, y1] = opts.ymin != null ? [opts.ymin, opts.ymax] : echelle(pts.map(p => p.y).concat([0]));
  const X = v => M + (v - x0) / (x1 - x0) * (W - 2 * M), Y = v => H - M - (v - y0) / (y1 - y0) * (H - 2 * M);
  svg.append(sv("rect", {x: M, y: M, width: W - 2 * M, height: H - 2 * M, fill: "none", stroke: gyCouleur("--border","#2a2a2a")}));
  svg.append(sv("line", {x1: X(0), y1: M, x2: X(0), y2: H - M, stroke: gyCouleur("--bordure-forte","#555"), "stroke-dasharray": "4 4"}));
  svg.append(sv("line", {x1: M, y1: Y(0), x2: W - M, y2: Y(0), stroke: gyCouleur("--bordure-forte","#555"), "stroke-dasharray": "4 4"}));
  if (opts.cercle) svg.append(sv("ellipse", {cx: X(0), cy: Y(0), rx: Math.abs(X(1) - X(0)), ry: Math.abs(Y(1) - Y(0)), fill: "none", stroke: gyCouleur("--bordure-forte","#666")}));
  svg.append(sv("text", {x: W - M, y: H - 10, "text-anchor": "end", fill: gyCouleur("--texte-discret","#999"), "font-size": 11}, opts.libX || ""));
  svg.append(sv("text", {x: M + 4, y: M - 8, fill: gyCouleur("--texte-discret","#999"), "font-size": 11}, opts.libY || ""));
  const pal = gyPaletteSuite(pts.length);   // un point (ou une flèche) = une couleur hexadécimale, différente de celle des autres
  const etiquettes = [];
  pts.forEach((p, i) => {
    const c = pal[i], r = p.rayon || 3;
    let forme;
    if (opts.fleches) forme = sv("line", {x1: X(0), y1: Y(0), x2: X(p.x), y2: Y(p.y), stroke: c, "stroke-width": 1.5});
    else if (p.carre) forme = sv("rect", {x: X(p.x) - r, y: Y(p.y) - r, width: 2 * r, height: 2 * r, fill: c, "fill-opacity": p.gras ? 1 : 0.55});
    else forme = sv("circle", {cx: X(p.x), cy: Y(p.y), r, fill: c, "fill-opacity": p.gras ? 1 : 0.55});
    svg.append(forme);
    if (p.nom) { forme.append(sv("title", {}, p.nom)); etiquettes.push({p, c, r, i}); }   // le nom reste lisible au survol même si son étiquette est masquée
  });
  // Étiquettes SANS chevauchement : des points aux coordonnées voisines (modalités supplémentaires près de l'origine…) écrivaient tous leur nom au
  // même endroit. Chaque étiquette cherche une place libre autour de son point (8 positions, puis décalée avec un trait de rappel) ; si aucune
  // n'est libre elle est masquée (nom au survol). Les étiquettes en gras sont placées d'abord.
  const poses = [];
  const lw = t => t ? t.length * 0.62 * 12 : 0;
  const occupe = [{x: W - M - lw(opts.libX || "") * 0.92, y: H - 23, w: lw(opts.libX || "") * 0.92, h: 13}, {x: M + 4, y: M - 19, w: lw(opts.libY || "") * 0.92, h: 13}];
  const libre = b => b.x >= M && b.x + b.w <= W - M + 20 && b.y >= 4 && b.y + b.h <= H - 4 && !occupe.some(q => b.x < q.x + q.w && b.x + b.w > q.x && b.y < q.y + q.h && b.y + b.h > q.y);
  let masquees = 0;
  etiquettes.sort((a, b) => (b.p.gras ? 1 : 0) - (a.p.gras ? 1 : 0) || a.i - b.i).forEach(({p, c, r}) => {
    const fs = p.gras ? 12 : 10, w = p.nom.length * 0.62 * fs, h = fs + 2, px = X(p.x), py = Y(p.y), d = r + 3;
    const essais = [[d, -h / 2], [d, -h - d / 2], [d, d / 2], [-d - w, -h / 2], [-d - w, -h - d / 2], [-d - w, d / 2], [-w / 2, -h - d], [-w / 2, d]];
    for (let k = 1; k <= 3; k++) essais.push([d + 6, -h / 2 - k * (h + 1)], [d + 6, -h / 2 + k * (h + 1)], [-d - 6 - w, -h / 2 - k * (h + 1)], [-d - 6 - w, -h / 2 + k * (h + 1)]);
    const choix = essais.map(([dx, dy], n) => ({x: px + dx, y: py + dy, w, h, loin: n >= 8})).find(libre);
    if (!choix) { masquees++; return; }
    occupe.push(choix);
    if (choix.loin) svg.append(sv("line", {x1: px, y1: py, x2: choix.x + (choix.x > px ? 0 : w), y2: choix.y + h / 2, stroke: c, "stroke-width": 0.6, "stroke-opacity": 0.6}));
    svg.append(sv("text", {x: choix.x, y: choix.y + h - 3, fill: c, "font-size": fs, "font-weight": p.gras ? 700 : 400}, p.nom));
  });
  if (masquees) svg.append(sv("text", {x: W - M - 4, y: M + 12, "text-anchor": "end", fill: gyCouleur("--texte-discret","#999"), "font-size": 10}, `+${masquees}`));
  if (pts.length) G.legende(svg, opts.fleches ? [[pal[0], "Variables (une flèche par variable, nom écrit à sa couleur)", "ligne"]] : [[pal[0], "Observations (un point par ligne)", "point"]].concat(pts.some(p => p.carre) ? [[pal[Math.max(0, pts.findIndex(p => p.carre))], "Modalités supplémentaires (carrés)", "carre"]] : []));
  return el("div", {class: "sx-graphe"}, svg);
};
// Éboulis : barres des valeurs propres (% d'inertie) + courbe cumulée.
G.eboulis = function (vps) {
  const W = 560, H = 200, M = 34, n = vps.length;
  const svg = sv("svg", {viewBox: `0 0 ${W} ${H}`, class: "sx-svg", role: "img"});
  const mx = Math.max(...vps.map(v => v.pct), 1), bw = (W - 2 * M) / n, pal = gyPaletteSuite(n + 1);   // une couleur par barre + une pour la courbe cumulée
  vps.forEach((v, i) => {
    const h = v.pct / mx * (H - 2 * M);
    svg.append(sv("rect", {x: M + i * bw + 4, y: H - M - h, width: bw - 8, height: h, fill: pal[i]}));
    svg.append(sv("text", {x: M + i * bw + bw / 2, y: H - M + 14, "text-anchor": "middle", fill: gyCouleur("--texte-discret","#999"), "font-size": 11}, "F" + v.axe));
    svg.append(sv("text", {x: M + i * bw + bw / 2, y: H - M - h - 4, "text-anchor": "middle", fill: gyCouleur("--texte-clair","#d4d4d4"), "font-size": 10}, fmt(Math.round(v.pct * 10) / 10) + "%"));
  });
  svg.append(gyLigneDegrade(vps.map((v, i) => [M + i * bw + bw / 2, H - M - v.pct_cumule / 100 * (H - 2 * M)]), pal[n], {"stroke-width": 2}));   // inertie cumulée = évolution : dégradé du plus sombre au plus clair
  G.legende(svg, vps.map((v, i) => [pal[i], "F" + v.axe + " : " + fmt(Math.round(v.pct * 10) / 10) + " % de la variance", "carre"]).concat([[pal[n], "% cumulé de variance", "ligne"]]));
  return el("div", {class: "sx-graphe"}, svg);
};
// Choix des axes du plan (liste déroulante X / Y).
G.choixAxes = function (n, defaut, quand) {
  const mk = (i) => el("select", {}, ...Array.from({length: n}, (_, k) => el("option", {value: k, ...(k === defaut[i] ? {selected: "selected"} : {})}, "Axe " + (k + 1))));
  const sx = mk(0), sy = mk(1);
  const go = () => quand(Number(sx.value), Number(sy.value));
  sx.onchange = go; sy.onchange = go;
  return {boite: el("div", {class: "sx-barre"}, el("label", {class: "geo-champ", style: "max-width:160px"}, "Horizontal", sx), el("label", {class: "geo-champ", style: "max-width:160px"}, "Vertical", sy)), go};
};

dessinerTables();
const cour = document.querySelector(`#sx-tables input[data-tout="${C.tableId}"]`);
if (cour) { cour.checked = true; document.querySelectorAll(`#sx-tables input[data-table="${C.tableId}"]`).forEach(i => { i.checked = true; }); majMemoire(); }
})();
