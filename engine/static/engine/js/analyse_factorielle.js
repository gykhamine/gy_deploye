// Page Analyse factorielle : affichage de l'ACP / ACM / AFC automatique (engine/analyses_auto.py).
(function () {
"use strict";
const G = window.GYAnalyse, {el, fmt, fmtPct, carte, sous, tableau} = G;
const r3 = v => v == null ? null : Math.round(v * 1000) / 1000;
const pct = v => v == null ? null : fmtPct(v);

function tableValeursPropres(vps, colonnesExtra) {
  const ent = ["Axe", tj("engine/static/engine/js/analyse_factorielle.js:9:24"), tj("engine/static/engine/js/analyse_factorielle.js:9:41"), tj("engine/static/engine/js/analyse_factorielle.js:9:56"), ...colonnesExtra.map(c => c[0])];
  return tableau(ent, vps.map(v => [v.axe, r3(v.valeur), pct(v.pct), pct(v.pct_cumule), ...colonnesExtra.map(c => c[1](v))]));
}
const oui = b => b ? "✔" : "";

// Plan factoriel interactif : choix des deux axes, chaque graphique est redessiné à la demande.
function plan(zone, nAxes, fabrique) {
  const dessus = el("div", {});
  const {boite, go} = G.choixAxes(Math.min(nAxes, 5), [0, Math.min(1, nAxes - 1)], (i, j) => { dessus.textContent = ""; dessus.append(...fabrique(i, j)); });
  zone.append(boite, dessus);
  go();
}
const lib = (vps, k) => tj("engine/static/engine/js/analyse_factorielle.js:lbl_axe", {v1: k + 1, v2: fmtPct(vps[k].pct)});

function rendreACP(a) {
  const c = carte(tj("engine/static/engine/js/analyse_factorielle.js:titre_acp", {v1: a.p, v2: a.n}), el("span", {class: "sx-badge"}, a.reduite ? tj("engine/static/engine/js/analyse_factorielle.js:24:108") : tj("engine/static/engine/js/analyse_factorielle.js:24:140")));
  const ad = a.adequation || {};
  if (ad.kmo != null || ad.bartlett) c.append(el("div", {class: "sx-info"}, (ad.kmo != null ? `KMO = ${r3(ad.kmo)} : ${ad.verdict}. ` : "") + (ad.bartlett ? tj("engine/static/engine/js/analyse_factorielle.js:26:159", {v1: fmt(ad.bartlett.chi2), v2: G.fmtP(ad.bartlett.p)}) : "")));
  c.append(el("div", {class: "sx-conclusion sig"}, tj("engine/static/engine/js/analyse_factorielle.js:27:53", {v1: a.axes_retenus.kaiser, v2: a.axes_retenus.baton_brise}) +
    tj("engine/static/engine/js/analyse_factorielle.js:28:6", {v1: a.axes_retenus.kaiser, v2: fmtPct(a.valeurs_propres[a.axes_retenus.kaiser - 1].pct_cumule)})));
  c.append(sous(tj("engine/static/engine/js/analyse_factorielle.js:29:18")), G.eboulis(a.valeurs_propres), tableValeursPropres(a.valeurs_propres, [["Kaiser", v => oui(v.kaiser)], [tj("engine/static/engine/js/analyse_factorielle.js:29:151"), v => oui(v.baton)]]));
  const na = a.valeurs_propres.length;
  c.append(sous(tj("engine/static/engine/js/analyse_factorielle.js:31:18")));
  c.append(tableau(["Variable", ...a.valeurs_propres.slice(0, 5).flatMap(v => [tj("engine/static/engine/js/analyse_factorielle.js:lbl_corr", {v1: v.axe}), `cos² F${v.axe}`, tj("engine/static/engine/js/analyse_factorielle.js:lbl_contrib", {v1: v.axe})])],
    a.variables.map(v => [v.nom, ...a.valeurs_propres.slice(0, 5).flatMap((_, k) => [r3(v.coord[k]), r3(v.cos2[k]), fmt(Math.round(v.contrib[k] * 10) / 10)])])));
  const zone = el("div", {});
  c.append(sous(tj("engine/static/engine/js/analyse_factorielle.js:35:18")), zone);
  plan(zone, na, (i, j) => {
    const cercle = G.nuage(a.variables.map((v, k) => ({x: v.coord[i], y: v.coord[j], nom: v.nom, couleur: G.couleur(k), gras: true})),
      {xmin: -1.1, xmax: 1.1, ymin: -1.1, ymax: 1.1, cercle: true, fleches: true, libX: lib(a.valeurs_propres, i), libY: lib(a.valeurs_propres, j)});
    const pts = a.individus.map(p => ({x: p.coord[i], y: p.coord[j], rayon: 2.5, couleur: gyCouleur("--accent","#6bcfff")}));
    (a.supplementaires || []).forEach((s, k) => pts.push({x: s.coord[i], y: s.coord[j], nom: `${s.modalite}`, couleur: gyCouleur("--forme-orange","#f5a524"), gras: true, rayon: 5}));
    return [cercle, el("div", {class: "sx-sous"}, tj("engine/static/engine/js/analyse_factorielle.js:41:52", {v1: a.individus.length, v2: a.individus_total}) + ((a.supplementaires || []).length ? tj("engine/static/engine/js/analyse_factorielle.js:41:158") : "")),
      G.nuage(pts, {libX: lib(a.valeurs_propres, i), libY: lib(a.valeurs_propres, j)})];
  });
  if ((a.individus_extremes || []).length) c.append(sous(tj("engine/static/engine/js/analyse_factorielle.js:44:59")), tableau(["Axe", tj("engine/static/engine/js/analyse_factorielle.js:44:135"), tj("engine/static/engine/js/analyse_factorielle.js:44:144"), "Contribution"], a.individus_extremes.map(t => ["F" + t.axe, t.ligne, r3(t.coord), pct(t.contrib)])));
  return c;
}

function tableModalites(lignes, vps, avecVar) {
  const k = Math.min(vps.length, 3);
  return tableau([...(avecVar ? ["Variable"] : []), tj("engine/static/engine/js/analyse_factorielle.js:50:54"), tj("engine/static/engine/js/analyse_factorielle.js:lbl_effectif"), ...Array.from({length: k}, (_, i) => [`F${i + 1}`, tj("engine/static/engine/js/analyse_factorielle.js:lbl_contrib", {v1: i + 1}), `cos² F${i + 1}`]).flat()],
    lignes.map(m => [...(avecVar ? [m.variable] : []), m.modalite, m.n, ...Array.from({length: k}, (_, i) => [r3(m.coord[i]), pct(m.contrib[i]), r3(m.cos2[i])]).flat()]));
}

function rendreACM(a) {
  const c = carte(tj("engine/static/engine/js/analyse_factorielle.js:55:20", {v1: a.variables_n, v2: a.modalites_n, v3: a.n}));
  c.append(el("div", {class: "sx-conclusion sig"}, tj("engine/static/engine/js/analyse_factorielle.js:56:53", {v1: a.axes_retenus.moyenne, v2: r3(a.valeur_propre_moyenne)})));
  c.append(sous(tj("engine/static/engine/js/analyse_factorielle.js:57:18")), G.eboulis(a.valeurs_propres), tableValeursPropres(a.valeurs_propres, [[tj("engine/static/engine/js/analyse_factorielle.js:57:128"), v => pct(v.benzecri)], ["Greenacre", v => pct(v.greenacre)]]));
  c.append(sous(tj("engine/static/engine/js/analyse_factorielle.js:58:18")),
    tableau(["Variable", ...a.valeurs_propres.slice(0, 5).map(v => "F" + v.axe)], a.eta2.map(e => [e.nom, ...e.eta2.slice(0, 5).map(r3)])));
  const vars = Array.from(new Set(a.modalites.map(m => m.variable)));
  const zone = el("div", {});
  c.append(sous(tj("engine/static/engine/js/analyse_factorielle.js:62:18")), zone);
  plan(zone, a.valeurs_propres.length, (i, j) => [
    G.nuage(a.modalites.map(m => ({x: m.coord[i], y: m.coord[j], nom: vars.length > 1 ? `${m.variable}=${m.modalite}` : m.modalite, couleur: G.couleur(vars.indexOf(m.variable)), gras: true, rayon: 5})), {libX: lib(a.valeurs_propres, i), libY: lib(a.valeurs_propres, j)}),
    el("div", {class: "sx-sous"}, tj("engine/static/engine/js/analyse_factorielle.js:65:36", {v1: a.individus.length, v2: a.individus_total})),
    G.nuage(a.individus.map(p => ({x: p.coord[i], y: p.coord[j], rayon: 2.5})), {libX: lib(a.valeurs_propres, i), libY: lib(a.valeurs_propres, j)})]);
  c.append(sous(tj("engine/static/engine/js/analyse_factorielle.js:67:18")), tableModalites(a.modalites, a.valeurs_propres, true));
  return c;
}

function rendreAFC(a) {
  const k = a.khi2, c = carte(tj("engine/static/engine/js/analyse_factorielle.js:titre_afc", {v1: a.variable_lignes, v2: a.variable_colonnes, v3: a.n}));
  c.append(el("div", {class: "sx-conclusion " + (k.p < 0.05 ? "sig" : "nonsig")},
    k.p < 0.05 ? tj("engine/static/engine/js/analyse_factorielle.js:74:19", {v1: fmt(k.valeur), v2: k.ddl, v3: G.fmtP(k.p), v4: r3(k.cramer)})
      : tj("engine/static/engine/js/analyse_factorielle.js:75:10", {v1: fmt(k.valeur), v2: k.ddl, v3: G.fmtP(k.p), v4: r3(k.cramer)})));
  c.append(sous(tj("engine/static/engine/js/analyse_factorielle.js:76:18")), G.eboulis(a.valeurs_propres), tableValeursPropres(a.valeurs_propres, []));
  const zone = el("div", {});
  c.append(sous(tj("engine/static/engine/js/analyse_factorielle.js:78:18")), zone);
  const lib2 = (i) => lib(a.valeurs_propres, i);
  plan(zone, a.valeurs_propres.length, (i, j) => [G.nuage([
    ...a.lignes.map(m => ({x: m.coord[i], y: m.coord[j], nom: m.modalite, couleur: gyCouleur("--accent","#6bcfff"), gras: true, rayon: 5})),
    ...a.colonnes.map(m => ({x: m.coord[i], y: m.coord[j], nom: m.modalite, carre: true, gras: true, rayon: 5}))], {libX: lib2(i), libY: lib2(j)})]);
  c.append(sous(tj("engine/static/engine/js/analyse_factorielle.js:83:18", {v1: a.variable_lignes})), tableModalites(a.lignes, a.valeurs_propres, false), sous(tj("engine/static/engine/js/analyse_factorielle.js:83:117", {v1: a.variable_colonnes})), tableModalites(a.colonnes, a.valeurs_propres, false));
  const t = a.tableau;
  c.append(sous(tj("engine/static/engine/js/analyse_factorielle.js:85:18")),
    tableau(["", ...t.colonnes], t.lignes.map((l, i) => [l, ...t.colonnes.map((_, j) => {
      const r = t.residus[i][j];
      return el("span", {style: Math.abs(r) > 2 ? `color:${r > 0 ? gyCouleur("--ok","#2ecc71") : gyCouleur("--danger","#e74c3c")};font-weight:700` : ""}, `${t.effectifs[i][j]} (${(Math.round(r * 10) / 10).toString().replace(".", ",")})`);
    })])));
  return c;
}

function dessiner(d) {
  const a = d.analyse, sortie = [...G.contexte(d)];
  sortie.push(el("div", {class: "sx-info"}, tj("engine/static/engine/js/analyse_factorielle.js:95:46", {v1: d.methode.toUpperCase(), v2: d.pourquoi})));
  for (const x of G.avis(a.avertissements)) sortie.push(x);
  sortie.push(d.methode === "acp" ? rendreACP(a) : d.methode === "acm" ? rendreACM(a) : rendreAFC(a));
  return sortie;
}

G.$("lancer").onclick = () => G.lancer("analyse-factorielle/analyse", G.parametres({methode: G.$("o-methode").value, reduire: G.$("o-reduire").checked ? "1" : "0"}), dessiner);
})();
