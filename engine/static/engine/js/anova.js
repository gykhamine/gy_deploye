// Page ANOVA : affichage de l'ANOVA automatique (engine/analyses_auto.anova_auto) — test choisi et justifié, hypothèses, comparaisons, graphiques.
(function () {
"use strict";
const G = window.GYAnalyse, {el, sv, fmt, fmtP, carte, sous, tableau} = G;

function graphGroupes(groupes) {   // boîtes (min-max, quartiles, médiane) + moyenne et son intervalle de confiance à 95 %
  const W = 560, H = 260, M = 36, n = groupes.length;
  const svg = sv("svg", {viewBox: `0 0 ${W} ${H}`, class: "sx-svg", role: "img"});
  let a = Math.min(...groupes.map(g => g.min)), b = Math.max(...groupes.map(g => g.max));
  if (a === b) { a -= 1; b += 1; }
  const Y = v => H - M - (v - a) / (b - a) * (H - 2 * M), bw = (W - 2 * M) / n, pal = gyPaletteSuite(5 * n);   // par groupe : moustache, boîte, médiane, intervalle de confiance, moyenne = 5 couleurs
  svg.append(sv("line", {x1: M, y1: H - M, x2: W - M, y2: H - M, stroke: gyCouleur("--bordure-forte","#444")}));
  for (const t of [a, (a + b) / 2, b]) svg.append(sv("text", {x: M - 4, y: Y(t) + 4, "text-anchor": "end", fill: gyCouleur("--texte-discret","#999"), "font-size": 10}, fmt(t)));
  groupes.forEach((g, i) => {
    const cx = M + i * bw + bw / 2, w = Math.min(46, bw * 0.5), cm = pal[i], cb = pal[n + i], cd = pal[2 * n + i], ci = pal[3 * n + i], cy = pal[4 * n + i];
    svg.append(sv("line", {x1: cx, y1: Y(g.min), x2: cx, y2: Y(g.max), stroke: cm, "stroke-width": 1.6}));
    svg.append(sv("rect", {x: cx - w / 2, y: Y(g.q3), width: w, height: Math.max(1, Y(g.q1) - Y(g.q3)), fill: cb, "fill-opacity": 0.35, stroke: cb}));
    svg.append(sv("line", {x1: cx - w / 2, y1: Y(g.mediane), x2: cx + w / 2, y2: Y(g.mediane), stroke: cd, "stroke-width": 2}));
    if (g.ic_bas != null) svg.append(sv("line", {x1: cx + w / 2 + 8, y1: Y(g.ic_bas), x2: cx + w / 2 + 8, y2: Y(g.ic_haut), stroke: ci, "stroke-width": 2}));
    svg.append(sv("circle", {cx: cx + w / 2 + 8, cy: Y(g.moyenne), r: 4, fill: cy}));
    svg.append(sv("text", {x: cx, y: H - M + 14, "text-anchor": "middle", fill: gyCouleur("--texte-clair","#d4d4d4"), "font-size": 11}, g.nom.length > 14 ? g.nom.slice(0, 13) + "…" : g.nom));
    svg.append(sv("text", {x: cx, y: H - M + 27, "text-anchor": "middle", fill: gyCouleur("--texte-discret","#888"), "font-size": 10}, "n=" + g.n));
  });
  G.legende(svg, groupes.map((g, i) => [pal[n + i], g.nom + " (boîte Q1–Q3)", "carre"]).concat([[pal[0], "Trait fin : minimum → maximum", "ligne"], [pal[2 * n], "Trait épais : médiane", "ligne"], [pal[3 * n], "Barre : intervalle de confiance à 95 % de la moyenne", "ligne"], [pal[4 * n], "Point : moyenne", "point"]]));
  return el("div", {class: "sx-graphe"}, sous(tj("engine/static/engine/js/anova.js:24:48")), svg);
}

function graphInteraction(c) {   // moyennes par modalité du 1er facteur, une courbe par modalité du 2e : courbes non parallèles = interaction
  const W = 560, H = 260, M = 40, n = c.x.length;
  const svg = sv("svg", {viewBox: `0 0 ${W} ${H}`, class: "sx-svg", role: "img"});
  const vals = c.series.flatMap(s => s.moyennes).filter(v => v != null);
  let a = Math.min(...vals), b = Math.max(...vals);
  if (a === b) { a -= 1; b += 1; }
  const X = i => M + (n === 1 ? 0.5 : i / (n - 1)) * (W - 2 * M - 80), Y = v => H - M - (v - a) / (b - a) * (H - 2 * M);
  c.x.forEach((m, i) => svg.append(sv("text", {x: X(i), y: H - M + 16, "text-anchor": "middle", fill: gyCouleur("--texte-clair","#d4d4d4"), "font-size": 11}, m.length > 12 ? m.slice(0, 11) + "…" : m)));
  for (const t of [a, b]) svg.append(sv("text", {x: M - 4, y: Y(t) + 4, "text-anchor": "end", fill: gyCouleur("--texte-discret","#999"), "font-size": 10}, fmt(t)));
  const pal = gyPaletteSuite(c.series.length);
  c.series.forEach((s, k) => {
    const col = pal[k];
    svg.append(gyLigneDegrade(s.moyennes.map((v, i) => v == null ? null : [X(i), Y(v)]), col, {"stroke-width": 2}));   // moyennes d'une modalité à l'autre = évolution : dégradé de la couleur de la série
    s.moyennes.forEach((v, i) => { if (v != null) svg.append(sv("circle", {cx: X(i), cy: Y(v), r: 3.5, fill: col})); });
    svg.append(sv("text", {x: W - M - 70, y: M + k * 14, fill: col, "font-size": 11}, "— " + (s.nom.length > 14 ? s.nom.slice(0, 13) + "…" : s.nom)));
  });
  return el("div", {class: "sx-graphe"}, sous(tj("engine/static/engine/js/anova.js:42:48", {v1: c.facteur_x, v2: c.facteur_serie})), svg);
}

function ligneTest(cle, r, retenu) {
  return [el("span", {}, (cle === retenu ? "★ " : "") + r.test.split(" — ")[0]), r.statistique.nom + " = " + fmt(r.statistique.valeur), r.ddl, G.fmtP(r.p), r.significatif ? "significatif" : tj("engine/static/engine/js/anova.js:46:192")];
}

function carteFacteur(f) {
  const r = f.resultats[f.retenu];
  const c = el("div", {class: "sx-carte"}, el("div", {class: "st-titre"}, tj("engine/static/engine/js/anova.js:selon_facteur", {v1: f.facteur}), " ", el("span", {class: "sx-badge"}, tj("engine/static/engine/js/anova.js:nb_groupes_n", {v1: f.groupes.length, v2: f.n}))));
  c.append(el("div", {class: "sx-conclusion " + (r.significatif ? "sig" : "nonsig")}, f.synthese));
  c.append(el("div", {class: "sx-info"}, tj("engine/static/engine/js/anova.js:53:43") + r.test.split(" — ")[0] + " — " + f.raison));
  c.append(sous(tj("engine/static/engine/js/anova.js:54:18")), tableau(["Condition", "p", "Verdict"], f.hypotheses.map(h => [h.nom, h.p == null ? "—" : fmtP(h.p), el("span", {}, (h.ok ? "✔ " : "✘ ") + h.texte)])));
  c.append(sous(tj("engine/static/engine/js/anova.js:55:18")), tableau([tj("engine/static/engine/js/anova.js:55:74"), tj("engine/static/engine/js/anova.js:lbl_statistique"), tj("engine/static/engine/js/anova.js:lbl_ddl"), "p", "Verdict"], Object.entries(f.resultats).map(([k, v]) => ligneTest(k, v, f.retenu))));
  if (r.tableau_anova) c.append(sous(tj("engine/static/engine/js/anova.js:56:39")), tableau(["Source", tj("engine/static/engine/js/anova.js:56:95"), tj("engine/static/engine/js/anova.js:lbl_ddl"), tj("engine/static/engine/js/anova.js:56:122"), "F", "p"], r.tableau_anova.map(l => [l.source, l.ss, l.ddl, l.ms, l.f, l.p == null ? null : fmtP(l.p)])));
  if (r.effet) c.append(el("div", {class: "sx-info"}, `${r.effet.nom} : ${fmt(r.effet.valeur)}`));
  const paires = (r.comparaisons || {}).lignes || [];
  if (paires.length) c.append(sous((r.comparaisons || {}).libelle || tj("engine/static/engine/js/anova.js:59:71")), tableau([tj("engine/static/engine/js/anova.js:groupe_a"), tj("engine/static/engine/js/anova.js:groupe_b"), tj("engine/static/engine/js/anova.js:59:133"), tj("engine/static/engine/js/anova.js:59:154"), "Verdict"], paires.map(p => [p.a, p.b, p.difference, fmtP(p.p), p.significatif ? tj("engine/static/engine/js/anova.js:59:248") : tj("engine/static/engine/js/anova.js:59:277")])));
  c.append(sous(tj("engine/static/engine/js/anova.js:lbl_groupes")), tableau([tj("engine/static/engine/js/anova.js:lbl_groupe"), "n", tj("engine/static/engine/js/anova.js:60:54"), tj("engine/static/engine/js/anova.js:60:100"), tj("engine/static/engine/js/anova.js:lbl_ic_95_bas"), tj("engine/static/engine/js/anova.js:lbl_ic_95_haut"), tj("engine/static/engine/js/anova.js:60:110")], f.groupes.map(g => [g.nom, g.n, g.moyenne, g.ecart_type, g.ic_bas, g.ic_haut, g.mediane])), graphGroupes(f.groupes));
  for (const a of r.avertissements || []) c.append(el("div", {class: "sx-avert"}, a));
  return c;
}

function carteMulti(m) {
  const r = m.resultat;
  const c = el("div", {class: "sx-carte"}, el("div", {class: "st-titre"}, tj("engine/static/engine/js/anova.js:67:76") + m.facteurs.join(" × ")));
  c.append(el("div", {class: "sx-conclusion " + (r.significatif === false ? "nonsig" : "sig")}, m.synthese));
  const res = (r.tableau_anova || []).find(l => l.f == null && l.ms != null);
  if (r.tableau_anova) c.append(sous(tj("engine/static/engine/js/anova.js:70:39")), tableau(["Source", tj("engine/static/engine/js/anova.js:70:150"), tj("engine/static/engine/js/anova.js:lbl_ddl"), tj("engine/static/engine/js/anova.js:70:177"), "F", "p", tj("engine/static/engine/js/anova.js:lbl_eta_partiel")],
    r.tableau_anova.map(l => [l.source, l.ss, l.ddl, l.ms, l.f, l.p == null ? null : fmtP(l.p), l.f != null && res ? l.ss / (l.ss + res.ss) : null])));
  if (m.croise) c.append(graphInteraction(m.croise));
  for (const a of (m.avertissements || []).concat(r.avertissements || [])) c.append(el("div", {class: "sx-avert"}, a));
  return c;
}

function dessiner(d) {
  const sortie = [...G.contexte(d)];
  for (const a of d.analyses) {
    const bloc = el("div", {class: "sx-carte"}, el("div", {class: "st-titre"}, tj("engine/static/engine/js/anova.js:80:81") + a.reponse));
    for (const f of a.un_facteur) bloc.append(carteFacteur(f));
    if (a.multi) bloc.append(carteMulti(a.multi));
    for (const i of a.ignores || []) bloc.append(el("div", {class: "sx-avert"}, `${i.facteur} : ${i.raison}`));
    sortie.push(bloc);
  }
  for (const e of d.echecs || []) sortie.push(el("div", {class: "sx-avert"}, `${e.reponse} : ${e.raison}`));
  return sortie;
}

G.$("lancer").onclick = () => G.lancer("anova/analyse", G.parametres({alpha: G.$("o-alpha").value}), dessiner);
})();
