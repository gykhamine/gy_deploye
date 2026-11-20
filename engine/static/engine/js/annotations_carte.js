// ANNOTATIONS de la carte de géolocalisation : l'utilisateur marque des points, trace des trajectoires et dessine des zones pour approfondir
// son analyse. Tout est ENREGISTRÉ (table « Annotations cartographiques », API /api/tables/<id>/geolocalisation/annotations/, engine/geo_annotations.py)
// et partagé avec l'équipe. S'appuie sur window.GEO_API (geolocalisation.js), comme logistique.js.
(function () {
"use strict";
const A = window.GEO_API, G = window.GEO;
if (!A || !document.getElementById("an-liste")) return;
const $ = id => document.getElementById(id), el = A.el;
const URL_AN = "/api/tables/" + G.tableId + "/geolocalisation/annotations/";
const N = {liste: [], outil: null, pts: [], sel: null};
const LIB = {point: "Point", trajectoire: tj("engine/static/engine/js/annotations_carte.js:lbl_trajectoire"), zone: "Zone"}, MIN = {point: 1, trajectoire: 2, zone: 3};
const fmtSurface = v => v < 1e4 ? Math.round(v) + " m²" : v < 1e6 ? (v / 1e4).toFixed(2) + " ha" : (v / 1e6).toFixed(2) + " km²";
const visibles = () => A.S.couches.annotations !== false;
const symboleChoisi = () => $("an-aff") ? {affiliation: $("an-aff").value, dimension: $("an-dim").value, echelon: $("an-ech").value} : {};

// ── Nature d'un point ou d'une zone : STRATÉGIQUE, DANGER (alerte quand un objet s'approche) ou CONTEXTUELLE (mémoire : description durable) ──
// Tout est enregistré avec l'annotation (table « Annotations cartographiques ») : rien n'est volatil, la description et le rayon d'alerte reviennent à chaque ouverture.
const NAT = {strategique: {lib: tj("engine/static/engine/js/annotations_carte.js:18:34"), coul: gyCouleur("--forme-bleu", "#3b82f6"), icone: "★"}, danger: {lib: "Danger", coul: gyCouleur("--forme-rouge", "#e5484d"), icone: "⚠"}, contexte: {lib: tj("engine/static/engine/js/annotations_carte.js:lbl_contextuelle"), coul: gyCouleur("--forme-orange", "#f5a524"), icone: "ℹ"}};
const avecNature = a => !!(a && NAT[a.nature]);
const libelle = a => (avecNature(a) ? NAT[a.nature].icone + " " : "") + (a.titre || LIB[a.type]);
function fenetre(o) {   // petite fenêtre (pop-up) de saisie / consultation ; renvoie les valeurs saisies, {supprimer: true} ou null (annulé)
  return new Promise(resolve => {
    const simple = o.type === "trajectoire", lecture = !!o.lecture;
    const fin = v => { document.removeEventListener("keydown", touche, true); fond.remove(); resolve(v); };
    const champ = (titre, ...c) => el("label", {style: "display:block;margin:8px 0 2px;font-size:13px"}, titre, el("br"), ...c);
    const nat = el("select", {style: "width:100%;padding:8px"}, el("option", {value: ""}, tj("engine/static/engine/js/annotations_carte.js:26:92")), ...Object.entries(NAT).map(([k, v]) => el("option", {value: k}, v.icone + " " + v.lib)));
    const titre = el("input", {type: "text", maxlength: "200", autocomplete: "off", style: "width:100%;padding:8px;box-sizing:border-box"});
    const desc = el("textarea", {rows: "4", maxlength: "2000", placeholder: tj("engine/static/engine/js/annotations_carte.js:28:78"), style: "width:100%;padding:8px;box-sizing:border-box"});
    const rayon = el("input", {type: "number", min: "0", max: "100000", step: "10", style: "width:100%;padding:8px;box-sizing:border-box"});
    const alerte = el("input", {type: "checkbox"}), coul = el("input", {type: "color"});
    nat.value = o.nature || ""; titre.value = o.titre || ""; desc.value = o.note || ""; coul.value = o.couleur || gyCouleur("--forme-orange","#f5a524");
    rayon.value = o.nature ? String(o.rayon_m != null ? o.rayon_m : (o.type === "zone" ? 100 : 200)) : ""; alerte.checked = o.nature ? !!o.alerte : false;
    let couleurTouchee = !!o.id;   // une couleur déjà choisie n'est pas écrasée par le changement de nature
    coul.oninput = () => { couleurTouchee = true; };
    const montrer = () => { blocAlerte.style.display = nat.value ? "" : "none"; };
    nat.onchange = () => {
      if (!nat.value) { montrer(); return; }
      if (!couleurTouchee) coul.value = NAT[nat.value].coul;
      if (rayon.value === "") rayon.value = o.type === "zone" ? "100" : "200";
      alerte.checked = nat.value !== "contexte"; montrer();
    };
    [nat, titre, desc, rayon, alerte, coul].forEach(c => { if (lecture) c.disabled = true; });
    const blocAlerte = el("div", {}, champ(tj("engine/static/engine/js/annotations_carte.js:43:45"), rayon),
      el("label", {style: "display:block;margin:8px 0;font-size:13px"}, alerte, tj("engine/static/engine/js/annotations_carte.js:44:82")));
    const infos = (o.infos || []).map(t => el("div", {class: "geo-note", style: "margin:6px 0"}, t));
    const boutons = lecture ? [el("button", {class: "geo-btn principal", onclick: () => fin(null)}, tj("engine/static/engine/js/annotations_carte.js:46:102"))] : [
      el("button", {class: "geo-btn principal", style: "min-height:44px", onclick: () => {
        const r = parseFloat(String(rayon.value).replace(",", "."));
        if (nat.value && rayon.value !== "" && !(r >= 0 && r <= 100000)) { A.toast(tj("engine/static/engine/js/annotations_carte.js:49:85"), true); return; }
        fin({titre: titre.value.trim(), note: desc.value, couleur: coul.value, nature: simple ? "" : nat.value, rayon_m: nat.value && rayon.value !== "" ? r : undefined, alerte: nat.value ? alerte.checked : false});
      }}, o.id ? tj("engine/static/engine/js/annotations_carte.js:51:19") : tj("engine/static/engine/js/annotations_carte.js:51:35")),
      el("button", {class: "geo-btn", style: "min-height:44px", onclick: () => fin(null)}, tj("engine/static/engine/js/annotations_carte.js:52:93")),
      ...(o.id ? [el("button", {class: "geo-btn", style: "min-height:44px", onclick: () => fin({supprimer: true})}, tj("engine/static/engine/js/annotations_carte.js:53:118"))] : [])];
    const carte = el("div", {style: "background:var(--panel,#1b2430);color:var(--text,#e8eef5);border:1px solid var(--border,#3a4656);border-radius:10px;padding:14px;width:min(92vw,440px);max-height:90vh;overflow:auto;box-sizing:border-box;box-shadow:0 8px 30px color-mix(in srgb,var(--ombre,#000000) 50%,transparent)"},
      el("div", {style: "font-weight:bold;font-size:15px"}, (o.id ? tj("engine/static/engine/js/annotations_carte.js:55:70") : tj("engine/static/engine/js/annotations_carte.js:55:129")) + (o.type === "zone" ? "zone" : o.type === "trajectoire" ? "trajectoire" : "point")),
      ...(simple ? [] : [champ(tj("engine/static/engine/js/annotations_carte.js:lbl_nature"), nat)]), champ(tj("engine/static/engine/js/annotations_carte.js:56:57"), titre), champ(tj("engine/static/engine/js/annotations_carte.js:56:80"), desc), ...(simple ? [] : [blocAlerte]), champ(tj("engine/static/engine/js/annotations_carte.js:lbl_couleur"), coul), ...infos,
      el("div", {style: "display:flex;gap:8px;flex-wrap:wrap;margin-top:12px"}, ...boutons));
    const fond = el("div", {style: "position:fixed;inset:0;background:color-mix(in srgb,var(--forme-contour,#000) 55%,transparent);z-index:3000;display:flex;align-items:center;justify-content:center;padding:8px", onclick: e => { if (e.target === fond) fin(null); }}, carte);
    const touche = e => { if (e.key === "Escape") { e.stopPropagation(); fin(null); } };
    document.addEventListener("keydown", touche, true);
    document.body.append(fond); montrer(); if (!lecture) (o.id ? desc : titre).focus();
  });
}

// ── Symbologie militaire (conventions APP-6 / MIL-STD-2525 : cadre selon l'affiliation, indicateur d'échelon, lettre de dimension) ──
// Cadre : ami = rectangle, hostile = losange, neutre = carré, inconnu = trèfle ; pointillé = présumé / suspect / en attente. Pas d'icône de fonction (infanterie, blindé…).
const SYM_COUL = {ami: gyCouleur("--forme-bleu", "#80e0ff"), presume_ami: gyCouleur("--forme-bleu", "#80e0ff"), neutre: gyCouleur("--forme-vert", "#aaffaa"), hostile: gyCouleur("--forme-rouge", "#ff8080"), suspect: gyCouleur("--forme-rouge", "#ff8080"), inconnu: gyCouleur("--forme-jaune", "#ffff80"), en_attente: gyCouleur("--forme-jaune", "#ffff80")};
const SYM_ECH = {equipe: "\u2205", groupe: "\u2022", section: "\u2022\u2022", peloton: "\u2022\u2022\u2022", compagnie: "I", bataillon: "II", regiment: "III", brigade: "X", division: "XX",
  corps: "XXX", armee: "XXXX", groupe_armees: "XXXXX", region: "++"};
const SYM_DIM = {air: "A", mer: "S", sous_marin: "U", espace: "E", terre_equipement: "", terre_installation: "", terre_unite: ""};
function symbole(ctx, x, y, a, sel) {
  const k = sel ? 1.2 : 1, f = a.affiliation, w = 13 * k, h = 9.5 * k;
  ctx.save(); ctx.fillStyle = SYM_COUL[f] || gyCouleur("--forme-jaune", "#ffff80"); ctx.strokeStyle = sel ? gyCouleur("--forme-clair", "#fff") : gyCouleur("--forme-contour", "#000"); ctx.lineWidth = sel ? 3 : 2;
  ctx.setLineDash(["presume_ami", "suspect", "en_attente"].includes(f) ? [4, 3] : []); ctx.beginPath();
  let haut = h;
  if (f === "hostile" || f === "suspect") { haut = 13 * k; ctx.moveTo(x, y - haut); ctx.lineTo(x + haut, y); ctx.lineTo(x, y + haut); ctx.lineTo(x - haut, y); ctx.closePath(); }
  else if (f === "neutre") { haut = 10 * k; ctx.rect(x - haut, y - haut, 2 * haut, 2 * haut); }
  else if (f === "inconnu" || f === "en_attente") { haut = 11 * k; const r = 6 * k; [[0, -1], [1, 0], [0, 1], [-1, 0]].forEach(([dx, dy]) => { ctx.moveTo(x + dx * r + r, y + dy * r); ctx.arc(x + dx * r, y + dy * r, r, 0, 2 * Math.PI); }); }
  else ctx.rect(x - w, y - h, 2 * w, 2 * h);
  ctx.fill(); ctx.stroke(); ctx.setLineDash([]);
  const d = SYM_DIM[a.dimension]; if (d) { ctx.fillStyle = (getComputedStyle(document.body).getPropertyValue("--texte-noir").trim() || "#000"); ctx.font = "bold " + Math.round(12 * k) + "px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(d, x, y + 1); }
  if (a.dimension === "terre_equipement") { ctx.fillStyle = gyCouleur("--forme-contour", "#000"); ctx.beginPath(); ctx.arc(x, y, 2.5 * k, 0, 2 * Math.PI); ctx.fill(); }
  if (a.dimension === "terre_installation") { ctx.strokeStyle = gyCouleur("--forme-contour", "#000"); ctx.lineWidth = 1.5; ctx.strokeRect(x - 4 * k, y - 4 * k, 8 * k, 8 * k); }
  const e = SYM_ECH[a.echelon]; if (e) { ctx.font = "bold " + Math.round(12 * k) + "px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "alphabetic"; ctx.lineWidth = 3; ctx.strokeStyle = gyCouleur("--forme-contour", "#000"); ctx.strokeText(e, x, y - haut - 3); ctx.fillStyle = (getComputedStyle(document.body).getPropertyValue("--texte-sombre").trim() || "#fff"); ctx.fillText(e, x, y - haut - 3); }
  ctx.restore();
}

// ── Géométrie (écran : détection du clic ; lat/lon : analyse) ───────────────
function distSeg(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1], l = dx * dx + dy * dy, t = l ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l)) : 0;
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}
function dansPoly(p, poly) {   // lancer de rayon, p et poly en [lat, lon]
  let dedans = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++)
    if ((poly[i][0] > p[0]) !== (poly[j][0] > p[0]) && p[1] < (poly[j][1] - poly[i][1]) * (p[0] - poly[i][0]) / (poly[j][0] - poly[i][0]) + poly[i][1]) dedans = !dedans;
  return dedans;
}
function touche(a, px, py) {
  const e = a.geometrie.map(p => A.versEcran(p[0], p[1]));
  if (a.type === "point") return Math.hypot(e[0][0] - px, e[0][1] - py) < (a.affiliation ? 18 : 12);
  const n = a.type === "zone" ? e.length : e.length - 1;
  for (let i = 0; i < n; i++) if (distSeg([px, py], e[i], e[(i + 1) % e.length]) < 8) return true;   // zone : son bord (l'intérieur laisse cliquer les objets dessous)
  return false;
}

// ── Dessin ──────────────────────────────────────────────────────────────────
function chemin(ctx, pts, ferme) {
  ctx.beginPath();
  pts.forEach((p, i) => { const [x, y] = A.versEcran(p[0], p[1]); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
  if (ferme) ctx.closePath();
}
function dessiner(ctx) {
  if (!visibles() && !N.pts.length) return;
  ctx.lineJoin = "round"; ctx.font = "12px sans-serif";
  const liste = visibles() ? N.liste : [];
  const couleurTitre = gyCouleur("--texte-sombre", "#fff");   // lue une fois par image (et non une fois par annotation)
  liste.forEach(a => {
    const sel = a.id === N.sel, g = a.geometrie; if (!g.length) return;
    ctx.strokeStyle = a.couleur; ctx.fillStyle = a.couleur; ctx.lineWidth = sel ? 4 : 2.5;
    if (a.type === "point" && a.affiliation) { const [x, y] = A.versEcran(g[0][0], g[0][1]); symbole(ctx, x, y, a, sel); }
    else if (a.type === "point") {
      const [x, y] = A.versEcran(g[0][0], g[0][1]);
      if (avecNature(a) && a.rayon_m > 0) { const r = a.rayon_m / A.mParPx(g[0][0]); ctx.save(); ctx.globalAlpha = 0.14; ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.fill(); ctx.globalAlpha = 0.8; ctx.setLineDash(a.alerte ? [6, 4] : [2, 4]); ctx.lineWidth = 1.5; ctx.stroke(); ctx.restore(); ctx.strokeStyle = a.couleur; ctx.fillStyle = a.couleur; }
      ctx.beginPath(); ctx.arc(x, y, sel ? 9 : 7, 0, 2 * Math.PI); ctx.fill(); ctx.strokeStyle = sel ? gyCouleur("--forme-clair", "#fff") : gyCouleur("--forme-contour", "#000"); ctx.stroke();
    }
    else { chemin(ctx, g, a.type === "zone"); if (a.type === "zone") { ctx.globalAlpha = 0.18; ctx.fill(); ctx.globalAlpha = 1; } ctx.stroke(); }
    if (A.S.couches.etiquettes !== false && (a.titre || avecNature(a))) {
      const [x, y] = A.versEcran(...(a.type === "zone" ? [g.reduce((s, p) => s + p[0], 0) / g.length, g.reduce((s, p) => s + p[1], 0) / g.length] : g[0]));
      ctx.fillStyle = couleurTitre; ctx.fillText(libelle(a), x + 10, y - 9);
    }
  });
  if (N.pts.length) {   // tracé en cours
    ctx.strokeStyle = $("an-couleur").value; ctx.fillStyle = ctx.strokeStyle; ctx.lineWidth = 2; ctx.setLineDash([6, 4]);
    chemin(ctx, N.pts, N.outil === "zone" && N.pts.length > 2); ctx.stroke(); ctx.setLineDash([]);
    N.pts.forEach(p => { const [x, y] = A.versEcran(p[0], p[1]); ctx.beginPath(); ctx.arc(x, y, 4, 0, 2 * Math.PI); ctx.fill(); });
  }
  ctx.globalAlpha = 1;
}

// ── Données ─────────────────────────────────────────────────────────────────
async function charger() {
  try {
    const d = await (await fetch(URL_AN)).json();
    N.liste = d.annotations || []; if (N.sel && !N.liste.some(a => a.id === N.sel)) N.sel = null;
    rendre(); A.redessiner();
  } catch (e) { /* hors ligne : on réessaiera */ }
}
const poster = corps => A.api(URL_AN, {method: "POST", body: JSON.stringify(corps)});
async function valider() {
  if (N.pts.length < (MIN[N.outil] || 99)) { A.toast(tj("engine/static/engine/js/annotations_carte.js:150:55") + MIN[N.outil] + tj("engine/static/engine/js/annotations_carte.js:150:92") + LIB[N.outil].toLowerCase() + ".", true); return; }
  const outil = N.outil, pts = N.pts.slice();
  const v = await fenetre({type: outil, titre: $("an-titre").value, note: $("an-note").value, couleur: $("an-couleur").value});   // pop-up : nature, description, rayon d'alerte
  if (!v || v.supprimer) { N.pts = []; rendre(); A.redessiner(); return; }
  try {
    await poster({action: "creer", type: outil, geometrie: pts, ...v, ...symboleChoisi()});
    A.toast(tj("engine/static/engine/js/annotations_carte.js:156:14")); N.pts = []; $("an-titre").value = ""; $("an-note").value = "";
    if (v.alerte) demanderNotifications();
    await charger();
  } catch (e) { /* toast déjà affiché par api() */ }
}

// ── Interface ───────────────────────────────────────────────────────────────
function choisirOutil(outil) {
  N.outil = N.outil === outil ? null : outil; N.pts = []; N.sel = null;
  document.querySelectorAll("[data-outil]").forEach(b => b.classList.toggle("principal", b.dataset.outil === N.outil));
  document.getElementById("carte").classList.toggle("mesure", !!N.outil);
  rendre(); A.redessiner();
}
function analyse(a) {
  const D = A.S.D, lignes = [];
  if (a.longueur_m != null) lignes.push((a.type === "zone" ? tj("engine/static/engine/js/annotations_carte.js:171:63") : tj("engine/static/engine/js/annotations_carte.js:lbl_longueur_espace")) + A.fmtM(a.longueur_m));
  if (a.surface_m2 != null) lignes.push("Surface " + fmtSurface(a.surface_m2));
  if (D && !D.vide) {
    const k = o => Math.min(Math.round(A.S.k), o.lat.length - 1), vus = D.objets.filter(o => A.S.visibles[o.id]);
    const trajets = vus.filter(o => !o.mode || o.mode === "trajectoire").map(o => ({o, p: o.lat[k(o)] != null ? [o.lat[k(o)], o.lon[k(o)]] : null})).filter(x => x.p);
    if (a.type === "zone") {
      const dedans = trajets.filter(x => dansPoly(x.p, a.geometrie)), seuls = vus.filter(o => o.points).reduce((s, o) => s + o.points.filter(p => dansPoly(p, a.geometrie)).length, 0);
      lignes.push(tj("engine/static/engine/js/annotations_carte.js:178:20") + dedans.length + (dedans.length ? " — " + dedans.map(x => x.o.nom).join(", ") : ""));
      if (seuls) lignes.push(tj("engine/static/engine/js/annotations_carte.js:179:31") + seuls);
    } else if (a.type === "point" && trajets.length) {
      const proche = trajets.map(x => ({n: x.o.nom, d: A.hav(a.geometrie[0][0], a.geometrie[0][1], x.p[0], x.p[1])})).sort((u, v) => u.d - v.d)[0];
      lignes.push(tj("engine/static/engine/js/annotations_carte.js:182:20") + tj("engine/static/engine/js/annotations_carte.js:objet_a_distance", {v1: proche.n, v2: A.fmtM(proche.d)}));
    }
  }
  return lignes;
}
function rendre() {
  const liste = $("an-liste"), det = $("an-detail"), a = N.liste.find(x => x.id === N.sel);
  liste.textContent = ""; det.textContent = "";
  N.liste.forEach(x => liste.append(el("div", {class: "geo-objet"}, el("span", {class: "puce", style: "background:" + x.couleur}),
    el("span", {class: "nom", title: tj("engine/static/engine/js/annotations_carte.js:191:39"), onclick: () => choisir(x.id, true)}, libelle(x)),
    el("span", {class: "geo-note"}, (avecNature(x) ? NAT[x.nature].lib + " · " : "") + LIB[x.type] + (x.surface_m2 != null ? " · " + fmtSurface(x.surface_m2) : x.longueur_m != null ? " · " + A.fmtM(x.longueur_m) : "")))));
  if (!N.liste.length) liste.append(el("div", {class: "geo-note"}, tj("engine/static/engine/js/annotations_carte.js:193:69")));
  if ($("an-brouillon")) {
    $("an-brouillon").style.display = N.outil && N.outil !== "point" ? "" : "none";
    $("an-trace").textContent = N.outil ? (N.outil === "point" ? tj("engine/static/engine/js/annotations_carte.js:196:67") : N.pts.length + tj("engine/static/engine/js/annotations_carte.js:196:134")) : "";
  }
  if (!a) return;
  det.append(el("div", {}, el("b", {}, libelle(a) + " · " + LIB[a.type] + (avecNature(a) ? " · " + NAT[a.nature].lib : ""))));
  if (avecNature(a)) det.append(el("div", {class: "geo-note"}, a.alerte ? tj("engine/static/engine/js/annotations_carte.js:200:76") + (a.rayon_m || 0) + " m" : tj("engine/static/engine/js/annotations_carte.js:200:129")));
  analyse(a).forEach(t => det.append(el("div", {}, t)));
  if (a.sidc) det.append(el("div", {title: tj("engine/static/engine/js/annotations_carte.js:202:45")}, "SIDC " + a.sidc));
  if (a.note) det.append(el("div", {style: "margin-top:4px;white-space:pre-wrap"}, a.note));
  det.append(el("button", {class: "geo-btn principal", onclick: () => ouvrirFiche(a), title: tj("engine/static/engine/js/annotations_carte.js:204:95")}, tj("engine/static/engine/js/annotations_carte.js:204:152")));
  if ($("an-valider")) det.append(
    el("button", {class: "geo-btn", onclick: modifier, title: tj("engine/static/engine/js/annotations_carte.js:206:64")}, tj("engine/static/engine/js/annotations_carte.js:206:145")),
    el("button", {class: "geo-btn", onclick: supprimer}, tj("engine/static/engine/js/annotations_carte.js:207:59")));
}
function choisir(id, centrer) {
  const a = N.liste.find(x => x.id === id); N.sel = id; N.outil = null; N.pts = [];
  document.querySelectorAll("[data-outil]").forEach(b => b.classList.remove("principal")); document.getElementById("carte").classList.remove("mesure");
  if (a && $("an-titre")) { $("an-titre").value = a.titre; $("an-couleur").value = a.couleur; $("an-note").value = a.note;
    if ($("an-aff")) { $("an-aff").value = a.affiliation || ""; $("an-dim").value = a.dimension || ""; $("an-ech").value = a.echelon || ""; } }
  if (a && centrer) { const g = a.geometrie; A.centrerSur(g.reduce((s, p) => s + p[0], 0) / g.length, g.reduce((s, p) => s + p[1], 0) / g.length, a.type === "point" ? 15 : 13); }
  rendre(); A.redessiner();
}
async function ouvrirFiche(a) {   // pop-up de consultation / modification : la description reste enregistrée avec l'annotation
  const ecriture = !!$("an-valider");
  const v = await fenetre({id: a.id, type: a.type, titre: a.titre, note: a.note, couleur: a.couleur, nature: a.nature, rayon_m: a.rayon_m, alerte: a.alerte, lecture: !ecriture, infos: analyse(a)});
  if (!v) return;
  if (v.supprimer) { await supprimer(); return; }
  try { await poster({action: "modifier", id: a.id, ...v, nature: v.nature || "", ...symboleChoisi()}); A.toast(tj("engine/static/engine/js/annotations_carte.js:222:114")); if (v.alerte) demanderNotifications(); await charger(); } catch (e) { /* toast déjà affiché */ }
}
async function modifier() {
  try { await poster({action: "modifier", id: N.sel, titre: $("an-titre").value, couleur: $("an-couleur").value, note: $("an-note").value, ...symboleChoisi()}); A.toast(tj("engine/static/engine/js/annotations_carte.js:225:171")); await charger(); } catch (e) { /* toast déjà affiché */ }
}
async function supprimer() {
  if (!await GY.confirmer(tj("engine/static/engine/js/annotations_carte.js:228:17"))) return;
  try { await poster({action: "supprimer", id: N.sel}); N.sel = null; A.toast(tj("engine/static/engine/js/annotations_carte.js:229:80")); await charger(); } catch (e) { /* toast déjà affiché */ }
}
function clicCarte(px, py) {
  if (N.outil) {
    const [la, lo] = A.versGeo(px, py), dernier = N.pts[N.pts.length - 1];
    if (dernier && Math.hypot(...A.versEcran(dernier[0], dernier[1]).map((v, i) => v - [px, py][i])) < 4) return true;   // double-clic : un seul sommet
    N.pts = N.outil === "point" ? [[la, lo]] : N.pts.concat([[la, lo]]);
    if (N.outil === "point") valider(); else { rendre(); A.redessiner(); }
    return true;
  }
  if (!visibles()) return false;
  for (const a of N.liste.slice().reverse()) if (touche(a, px, py)) { choisir(a.id, false); ouvrirFiche(a); return true; }
  if (N.sel) { N.sel = null; rendre(); A.redessiner(); }
  return false;
}

// ── ALERTES D'APPROCHE : un objet suivi (ou votre GPS) entre dans le rayon d'un point / d'une zone stratégique ou de danger → bandeau, notification du système, vibration ──
function demanderNotifications() { try { if (window.GYNotif) GYNotif.demander(); else if (window.Notification && Notification.permission === "default") Notification.requestPermission(); } catch (e) { /* refusée : le bandeau reste */ } }
function distZone(p, poly) {   // mètres du point au bord du polygone (0 si dedans) : plan local, exact à l'échelle d'une ville
  if (dansPoly(p, poly)) return 0;
  const kx = Math.cos(p[0] * Math.PI / 180) * 111320, ky = 110574; let m = Infinity;
  for (let i = 0; i < poly.length; i++) { const a = poly[i], b = poly[(i + 1) % poly.length]; m = Math.min(m, distSeg([0, 0], [(a[1] - p[1]) * kx, (a[0] - p[0]) * ky], [(b[1] - p[1]) * kx, (b[0] - p[0]) * ky])); }
  return m;
}
function distAnnotation(a, p) { return a.type === "zone" ? distZone(p, a.geometrie) : A.hav(a.geometrie[0][0], a.geometrie[0][1], p[0], p[1]); }
function niveauApproche(a, p, ancien) {   // "dedans" | "proche" | "hors" (marge de 10 % pour ne pas clignoter au bord)
  const d = distAnnotation(a, p), r = Math.max(a.rayon_m || 0, a.type === "point" ? 25 : 0);
  if (a.type === "zone" && d === 0) return {n: "dedans", d};
  const seuil = ancien && ancien !== "hors" ? r * 1.1 + 5 : r;
  return {n: d <= seuil ? "proche" : "hors", d};
}
const etatApproche = new Map(), alertes = [];
function bandeau(texte, danger) {
  let b = $("an-bandeau");
  if (!b) { b = el("div", {id: "an-bandeau", style: "position:fixed;left:8px;right:8px;top:8px;z-index:2900;padding:12px 14px;border-radius:8px;font-weight:bold;color:var(--texte-sombre,#fff);box-shadow:0 4px 18px color-mix(in srgb,var(--ombre,#000000) 50%,transparent);cursor:pointer;display:none", onclick: () => { b.style.display = "none"; }}); document.body.append(b); }
  b.textContent = texte; b.style.background = danger ? gyCouleur("--forme-rouge", "#c62828") : gyCouleur("--forme-bleu", "#1e5fbf"); b.style.display = "block"; clearTimeout(b._t); b._t = setTimeout(() => { b.style.display = "none"; }, danger ? 15000 : 8000);
}
function prevenir(a, nom, niveau, d) {
  const nat = NAT[a.nature], danger = a.nature === "danger", note = a.note ? " — " + a.note.replace(/\s+/g, " ").slice(0, 140) : "";
  const titre = "« " + (a.titre || LIB[a.type]) + " »";
  const texte = nat.icone + " " + (danger ? tj("engine/static/engine/js/annotations_carte.js:danger") : a.nature === "strategique" ? tj("engine/static/engine/js/annotations_carte.js:269:89") : tj("engine/static/engine/js/annotations_carte.js:269:113")) + nom + (niveau === "dedans" ? tj("engine/static/engine/js/annotations_carte.js:269:158") + titre : tj("engine/static/engine/js/annotations_carte.js:approche", {v1: titre, v2: A.fmtM(d)})) + note;
  alertes.unshift({t: Date.now(), texte, danger}); alertes.length = Math.min(alertes.length, 30); majAlertes();
  if ($("an-alertes-actives") && !$("an-alertes-actives").checked) return;
  bandeau(texte, danger);
  try { document.dispatchEvent(new CustomEvent("geo-alerte-zone", {detail: {type: a.nature, message: texte, voix: (danger ? tj("engine/static/engine/js/annotations_carte.js:lbl_attention_danger") : "") + nom + (niveau === "dedans" ? tj("engine/static/engine/js/annotations_carte.js:273:185") : tj("engine/static/engine/js/annotations_carte.js:lbl_approche")) + (a.titre || "")}})); } catch (e) { /* sans effet */ }
  try {
    const titreAlerte = danger ? tj("engine/static/engine/js/annotations_carte.js:275:117") : tj("engine/static/engine/js/annotations_carte.js:275:138");
    if (window.GYNotif) GYNotif.afficher({titre: titreAlerte, message: texte, tag: "geo-zone-" + a.id, seulement_masquee: true});
    else if (document.hidden && window.Notification && Notification.permission === "granted") new Notification(titreAlerte, {body: texte, tag: "geo-zone-" + a.id});
    if (navigator.vibrate) navigator.vibrate(danger ? [300, 150, 300, 150, 300] : [200]);
  } catch (e) { /* notification système indisponible */ }
}
function surveiller(id, nom, lat, lon) {   // une position (objet suivi ou GPS) contre toutes les zones qui demandent une alerte
  const p = [lat, lon];
  N.liste.forEach(a => {
    if (!avecNature(a) || !a.alerte || !a.geometrie.length) return;
    const cle = a.id + "|" + id, ancien = etatApproche.get(cle), {n, d} = niveauApproche(a, p, ancien);
    etatApproche.set(cle, n);
    if (n !== "hors" && (ancien === undefined || ancien === "hors" || (ancien === "proche" && n === "dedans"))) prevenir(a, nom, n, d);
  });
}
function surveillerObjets() {
  const D = A.S.D; if (!D || D.vide || !N.liste.some(a => avecNature(a) && a.alerte)) return;
  const k = Math.max(0, Math.round(A.S.k));
  D.objets.filter(o => (!o.mode || o.mode === "trajectoire") && o.lat && A.S.visibles[o.id]).forEach(o => {
    for (let i = Math.min(o.lat.length - 1, k); i >= 0; i--) if (o.lat[i] != null && o.lon[i] != null) { surveiller("o:" + o.id, o.nom, o.lat[i], o.lon[i]); break; }
  });
}
function majAlertes() {
  let z = $("an-alertes"); if (!z) return;
  z.textContent = "";
  alertes.slice(0, 5).forEach(x => z.append(el("div", {class: "geo-note", style: x.danger ? "color:var(--texte-erreur,#ff8a80)" : ""}, new Date(x.t).toLocaleTimeString() + " · " + x.texte)));
  if (!alertes.length) z.append(el("div", {class: "geo-note"}, tj("engine/static/engine/js/annotations_carte.js:299:65")));
}
function croise(geometrie) {   // zones stratégiques / de danger (avec alerte) que le tracé d'un itinéraire traverse ou frôle : [{a, d}]
  if (!geometrie || geometrie.length < 2) return [];
  const pts = [];
  for (let i = 0; i < geometrie.length; i++) { pts.push(geometrie[i]); if (i) pts.push([(geometrie[i - 1][0] + geometrie[i][0]) / 2, (geometrie[i - 1][1] + geometrie[i][1]) / 2]); }
  return N.liste.filter(a => avecNature(a) && a.alerte && a.nature !== "contexte" && a.geometrie.length).map(a => {
    const r = Math.max(a.rayon_m || 0, a.type === "point" ? 25 : 0), d = Math.min(...pts.map(q => distAnnotation(a, q)));
    return d <= r ? {a, d} : null;
  }).filter(Boolean);
}
if ($("an-liste")) {
  const caseAlertes = el("input", {type: "checkbox", id: "an-alertes-actives"}); caseAlertes.checked = true;
  try { if (localStorage.getItem("geo-alertes-zones-" + G.tableId) === "0") caseAlertes.checked = false; } catch (e) { /* stockage indisponible */ }
  caseAlertes.onchange = () => { try { localStorage.setItem("geo-alertes-zones-" + G.tableId, caseAlertes.checked ? "1" : "0"); } catch (e) { /* sans effet */ } };
  $("an-liste").before(el("div", {class: "geo-note"}, el("label", {class: "geo-case", title: tj("engine/static/engine/js/annotations_carte.js:314:95")}, caseAlertes, tj("engine/static/engine/js/annotations_carte.js:314:209"))), el("div", {id: "an-alertes"}));
  majAlertes();
}
(window.GEO_APRES = window.GEO_APRES || []).push(() => surveillerObjets());
setInterval(surveillerObjets, 5000);

document.querySelectorAll("[data-outil]").forEach(b => { b.onclick = () => choisirOutil(b.dataset.outil); });
if ($("an-valider")) {
  $("an-valider").onclick = valider;
  $("an-retirer").onclick = () => { N.pts.pop(); rendre(); A.redessiner(); };
  $("an-abandonner").onclick = () => { N.pts = []; rendre(); A.redessiner(); };
  $("an-couleur").oninput = A.redessiner;
}
document.querySelectorAll("[data-couche=annotations]").forEach(c => c.addEventListener("change", A.redessiner));
$("an-export").onclick = () => {
  if (!N.liste.length) { A.toast(tj("engine/static/engine/js/annotations_carte.js:329:35"), true); return; }
  const f = N.liste.map(a => ({type: "Feature", properties: {titre: a.titre, type: a.type, nature: a.nature, rayon_m: a.rayon_m, alerte: a.alerte, note: a.note, couleur: a.couleur, affiliation: a.affiliation, dimension: a.dimension, echelon: a.echelon, sidc: a.sidc, longueur_m: a.longueur_m, surface_m2: a.surface_m2},
    geometry: a.type === "point" ? {type: "Point", coordinates: [a.geometrie[0][1], a.geometrie[0][0]]}
      : a.type === "trajectoire" ? {type: "LineString", coordinates: a.geometrie.map(p => [p[1], p[0]])}
      : {type: "Polygon", coordinates: [a.geometrie.concat(a.geometrie.slice(0, 1)).map(p => [p[1], p[0]])]}}));
  const l = el("a", {href: URL.createObjectURL(new Blob([JSON.stringify({type: "FeatureCollection", features: f})], {type: "application/geo+json"})), download: "annotations.geojson"});
  document.body.append(l); l.click(); l.remove();
};
document.addEventListener("keydown", e => {
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName) || !N.outil) return;
  if (e.key === "Enter" && N.outil !== "point") valider();
  else if (e.key === "Escape") choisirOutil(N.outil);
});
(window.GEO_DESSIN = window.GEO_DESSIN || []).push(Object.assign(dessiner, {calque: "annotations"}));
(window.GEO_CLIC = window.GEO_CLIC || []).push(clicCarte);
window.GEO_ANNOT = {recharger: charger, surveiller, croise, ouvrirFiche, alertes: () => alertes, liste: () => N.liste, natures: NAT};   // geo_outils.js enregistre ici les cercles, corridors et tracés mesurés
charger();
setInterval(() => { if (!document.hidden && !N.outil) charger(); }, 30000);   // annotations posées par un collègue
})();
