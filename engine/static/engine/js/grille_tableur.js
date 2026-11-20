// Grille de données façon tableur — clavier, presse-papiers, annulation, agrégats de sélection.
// Chargé après index_1.js, qui appelle gyGrille.brancher(ctx) à chaque affichage de la grille
// (voir _initGrilleEditionFusion). Utilise les globales d'index_1.js : _selection, _rafraichirSurbrillance,
// _demarrerEditionCellule, _ouvrirBarreFusion, api, afficherTable, notifierSucces, notifierErreur.
//
// Raccourcis (la grille a le focus après un clic sur une cellule) :
//   flèches / Tab / Maj+Tab        se déplacer        Maj+flèches      étendre la sélection
//   Ctrl+flèche                    aller au bord      Origine / Fin    début / fin de ligne
//   Entrée ou F2                   modifier           frappe directe   remplace le contenu
//   Suppr / Retour arrière         vider les cellules Échap            annuler la sélection
//   Ctrl+C                         copier (collable dans Excel)
//   Ctrl+V                         coller depuis Excel / un tableur (crée les lignes manquantes)
//   Ctrl+Z                         annuler la dernière modification    Ctrl+A   tout sélectionner
//   Sans clavier : la barre « gy-tactile » au-dessus de la grille porte un bouton par raccourci (Modifier, Vider, Annuler, Rétablir, Tout, Copier, Coller, Désélectionner)
//   et les boutons armés « Étendre / Ligne / Colonne » remplacent Maj/Ctrl + clic.
(() => {
  "use strict";
  const NUMERIQUES = new Set(["nombre", "decimal", "monnaie", "pourcentage", "note", "barre_progression", "constante"]);
  const BOOLEENS = new Set(["booleen", "switch"]);
  const NON_EDITABLES = new Set(["fichier", "image", "son", "video", "relation", "choix_multiple", "formule", "constante", "horodatage", "id_client"]);
  const MAX_ANNULATIONS = 50;
  let ctx = null;
  let grilleActive = false;  // la grille avait le focus avant sa dernière reconstruction
  const pileAnnulation = [];  // [{tableId, slug, libelle, operations:[{ligneId, valeurs}|{supprimerId}]}]

  const editable = (champ) => champ && !NON_EDITABLES.has(champ.type);
  const cellule = (r, c) => ctx && ctx.grille.querySelector(`td[data-ligne-idx="${r}"][data-col-idx="${c}"]`);
  const bornes = () => ({ rMin: ctx.offsetPage, rMax: ctx.offsetPage + ctx.nbLignes - 1, cMax: ctx.champs.length - 1 });
  const conteneur = () => ctx && ctx.grille.closest(".grille-tableur");

  // ── Normalisation des valeurs saisies ou collées (formats Excel français) ──────────────────────
  function normaliser(champ, brut) {
    let v = String(brut ?? "").replace(/\r/g, "");
    if (!champ) return v;
    const t = v.trim();
    if (NUMERIQUES.has(champ.type)) {
      const n = t.replace(/[\s  ]/g, "").replace(/[€$%]/g, "").replace(",", ".");
      return n !== "" && !isNaN(Number(n)) ? n : t;
    }
    if (BOOLEENS.has(champ.type)) {
      const b = t.toLowerCase();
      if (["1", "vrai", "true", "oui", "x", "yes"].includes(b)) return true;
      if (["0", "faux", "false", "non", "no", ""].includes(b)) return false;
      return t;
    }
    if (champ.type === "date") {
      const m = t.match(/^(\d{1,2})[\/.\-](\d{1,2})[\/.\-](\d{4})$/);  // 31/12/2026 -> 2026-12-31
      if (m) return `${m[3]}-${m[2].padStart(2, "0")}-${m[1].padStart(2, "0")}`;
    }
    if (champ.type === "datetime" || champ.type === "horodatage") {
      const m = t.match(/^(\d{1,2})[\/.\-](\d{1,2})[\/.\-](\d{4})[ T](\d{1,2}):(\d{2})/);
      if (m) return `${m[3]}-${m[2].padStart(2, "0")}-${m[1].padStart(2, "0")}T${m[4].padStart(2, "0")}:${m[5]}`;
    }
    return t;
  }

  function texteBrut(champ, ligne) {
    if (!ligne) return "";
    let v = ligne[champ.slug];
    if (champ.type === "relation" && ligne[champ.slug + "__label"]) v = ligne[champ.slug + "__label"];
    if (v === null || v === undefined) return "";
    if (typeof v === "boolean") return v ? "VRAI" : "FAUX";
    if (Array.isArray(v)) return v.join(", ");
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
  }

  // ── Sélection ────────────────────────────────────────────────────────────────────────────────
  function rectangle() {
    if (!_selection || !ctx || _selection.slug !== ctx.slug) return null;
    return {
      rMin: Math.min(_selection.r1, _selection.r2), rMax: Math.max(_selection.r1, _selection.r2),
      cMin: Math.min(_selection.c1, _selection.c2), cMax: Math.max(_selection.c1, _selection.c2),
    };
  }

  function selectionner(r1, c1, r2, c2) {
    _selection = { table: ctx.table, slug: ctx.slug, champs: ctx.champs, r1, c1, r2: r2 ?? r1, c2: c2 ?? c1 };
    _normaliserSelectionFusions();   // Maj+flèches : une fusion touchée est prise en entier, comme Excel
    _rafraichirSurbrillance(ctx.grille);
    _ouvrirBarreFusion(ctx.ligneParDispIndex);
    const td = cellule(_selection.r2, _selection.c2);
    if (td) td.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  function deplacer(dr, dc, etendre, auBord) {
    if (!ctx) return;
    refocaliser();
    const b = bornes();
    if (b.rMax < b.rMin) return;
    if (!_selection || _selection.slug !== ctx.slug) { selectionner(b.rMin, 0); return; }
    let r = etendre ? _selection.r2 : _selection.r1, c = etendre ? _selection.c2 : _selection.c1;
    if (auBord) {
      if (dr) r = dr > 0 ? b.rMax : b.rMin;
      if (dc) c = dc > 0 ? b.cMax : 0;
    } else {
      r += dr; c += dc;
      // Tab en fin de ligne : passe à la ligne suivante, comme Excel.
      if (dc && !dr && !etendre) {
        if (c > b.cMax && r < b.rMax) { c = 0; r += 1; }
        else if (c < 0 && r > b.rMin) { c = b.cMax; r -= 1; }
      }
      // Cellule cachée sous une fusion : on continue dans la même direction.
      let garde = 0;
      while (!cellule(Math.min(Math.max(r, b.rMin), b.rMax), Math.min(Math.max(c, 0), b.cMax)) && garde++ < 200) {
        r += dr; c += dc;
        if (r < b.rMin || r > b.rMax || c < 0 || c > b.cMax) break;
      }
    }
    r = Math.min(Math.max(r, b.rMin), b.rMax);
    c = Math.min(Math.max(c, 0), b.cMax);
    if (etendre) selectionner(_selection.r1, _selection.c1, r, c);
    else selectionner(r, c);
  }

  function refocaliser() {
    const el = conteneur();
    if (el && document.activeElement !== el && !(document.activeElement && document.activeElement.classList.contains("edition-cellule"))) {
      el.focus({ preventScroll: true });
    }
  }

  // ── Agrégats de la sélection (barre d'état d'Excel : somme, moyenne, nombre) ──────────────────
  function afficherAgregats() {
    const zone = document.getElementById("agregats-selection");
    const rect = rectangle();
    if (!zone || !rect) return;
    let n = 0, somme = 0, nb = 0, min = Infinity, max = -Infinity;
    for (let r = rect.rMin; r <= rect.rMax; r++) {
      const ligne = ctx.ligneParDispIndex[r];
      for (let c = rect.cMin; c <= rect.cMax; c++) {
        const champ = ctx.champs[c];
        const t = texteBrut(champ, ligne);
        if (t === "") continue;
        n++;
        if (NUMERIQUES.has(champ.type)) {
          const v = Number(String(t).replace(",", "."));
          if (!isNaN(v)) { somme += v; nb++; min = Math.min(min, v); max = Math.max(max, v); }
        }
      }
    }
    const f = (v) => v.toLocaleString((window.I18N_LOCALE||"fr-FR"), { maximumFractionDigits: 4 });
    zone.textContent = nb
      ? tj("engine/static/engine/js/grille_tableur.js:lbl_stats_selection", {v1: f(somme), v2: f(somme / nb), v3: f(min), v4: f(max), v5: n})
      : tj("engine/static/engine/js/grille_tableur.js:lbl_nb", {v1: n});
  }

  // ── Annulation (Ctrl+Z) ──────────────────────────────────────────────────────────────────────
  function memoriser(table, slug, operations, libelle) {
    if (!operations || !operations.length) return;
    pileAnnulation.push({ tableId: table.id, slug, libelle: libelle || "Modification", operations });
    if (pileAnnulation.length > MAX_ANNULATIONS) pileAnnulation.shift();
  }

  // Une transformation de colonne (remplacer, cloner, découper, lieux → coordonnées) est annulée PAR LE SERVEUR : son historique est
  // enregistré (il survit au rechargement). La pile locale ne garde qu'un repère pour que Ctrl+Z respecte l'ordre avec les modifications de cellules.
  function memoriserServeur(table, slug, libelle) {
    pileAnnulation.push({ tableId: table.id, slug, libelle: libelle || "Transformation", serveur: true, operations: [] });
    if (pileAnnulation.length > MAX_ANNULATIONS) pileAnnulation.shift();
  }

  async function transformationServeur(sens, tableId, slug) {   // sens : "annuler" | "refaire"
    const url = `/api/tables/${tableId}/transformations/${sens}/`;
    let r;
    try { r = await api(url, { method: "POST", body: JSON.stringify({}) }); }
    catch (e) {
      if (e.code !== "modifie_depuis" || !await GY.confirmer(tj("engine/static/engine/js/grille_tableur.js:165:52"))) return null;
      try { r = await api(url, { method: "POST", body: JSON.stringify({ forcer: true }) }); } catch (e2) { return null; }
    }
    const faits = r.colonnes_retirees ? tj("engine/static/engine/js/grille_tableur.js:168:42", {v1: r.colonnes_retirees.join(", ")}) : r.colonnes ? tj("engine/static/engine/js/grille_tableur.js:168:117", {v1: r.colonnes.join(", ")}) : tj("engine/static/engine/js/grille_tableur.js:168:170", {v1: r.restaurees});
    notifierSucces(`${sens === "annuler" ? tj("engine/static/engine/js/grille_tableur.js:169:45") : tj("engine/static/engine/js/grille_tableur.js:169:56")} : ${(r.libelle || "transformation").toLowerCase()} (${faits}${r.conservees_car_modifiees_depuis ? tj("engine/static/engine/js/grille_tableur.js:169:165", {v1: r.conservees_car_modifiees_depuis}) : ""}).`);
    if (slug === tableActive) afficherTable(slug);
    return r;
  }

  async function majBoutons(table) {   // titre et état des boutons ↶ / ↷ d'après le serveur
    const bA = document.getElementById("btn-annuler-grille"), bR = document.getElementById("btn-refaire-grille");
    if (!bA || !bR) return;
    try {
      const d = await (await fetch(`/api/tables/${table.id}/transformations/`)).json();
      bR.disabled = !d.peut_refaire;
      bR.title = d.peut_refaire ? tj("engine/static/engine/js/grille_tableur.js:180:36", {v1: d.prochain_refaire}) : tj("engine/static/engine/js/grille_tableur.js:180:82");
      if (d.peut_annuler) bA.title = tj("engine/static/engine/js/grille_tableur.js:181:39", {v1: d.prochain_annuler});
    } catch (e) { /* facultatif */ }
  }

  async function refaire() {
    const table = ctx && ctx.table;
    if (!table) return;
    await transformationServeur("refaire", table.id, ctx.slug);
  }

  async function annuler() {
    const action = pileAnnulation.pop();
    if (!action) {   // pile locale vide (page rechargée) : on retombe sur l'historique du serveur
      if (ctx && ctx.table) { const r = await transformationServeur("annuler", ctx.table.id, ctx.slug); if (r) return; }
      notifierErreur(tj("engine/static/engine/js/grille_tableur.js:195:23")); return;
    }
    if (action.serveur) { await transformationServeur("annuler", action.tableId, action.slug); return; }
    let ok = 0, echecs = 0;
    for (const op of action.operations.slice().reverse()) {
      try {
        if (op.supprimerId) {
          await api(`/api/tables/${action.tableId}/lignes/${op.supprimerId}/supprimer/`, { method: "POST" });
        } else {
          await api(`/api/tables/${action.tableId}/lignes/${op.ligneId}/modifier/`, {
            method: "POST", body: JSON.stringify(op.valeurs),
          });
        }
        ok++;
      } catch (e) { echecs++; }
    }
    notifierSucces(tj("engine/static/engine/js/grille_tableur.js:211:21", {v1: action.libelle.toLowerCase(), v2: ok, v3: echecs ? tj("engine/static/engine/js/grille_tableur.js:211:93", {v1: echecs}) : ""}));
    if (action.slug === tableActive) afficherTable(action.slug);
  }

  // ── Vider les cellules (Suppr) ───────────────────────────────────────────────────────────────
  async function viderSelection() {
    const rect = rectangle();
    if (!rect) return;
    const operations = [];
    for (let r = rect.rMin; r <= rect.rMax; r++) {
      const ligne = ctx.ligneParDispIndex[r];
      if (!ligne) continue;
      const nouvelles = {}, anciennes = {};
      for (let c = rect.cMin; c <= rect.cMax; c++) {
        const champ = ctx.champs[c];
        if (!editable(champ) || !cellule(r, c)) continue;
        const avant = ligne[champ.slug];
        if (avant === null || avant === undefined || avant === "") continue;
        nouvelles[champ.slug] = BOOLEENS.has(champ.type) ? false : "";
        anciennes[champ.slug] = avant;
      }
      if (!Object.keys(nouvelles).length) continue;
      try {
        await api(`/api/tables/${ctx.table.id}/lignes/${ligne._id}/modifier/`, { method: "POST", body: JSON.stringify(nouvelles) });
        operations.push({ ligneId: ligne._id, valeurs: anciennes });
      } catch (e) { /* déjà notifié */ }
    }
    if (operations.length) {
      memoriser(ctx.table, ctx.slug, operations, tj("engine/static/engine/js/grille_tableur.js:lbl_effacement"));
      afficherTable(ctx.slug);
    }
  }

  // ── Copier (Ctrl+C) : texte tabulé, collable tel quel dans Excel / LibreOffice ──────────────────
  function texteSelection() {
    const rect = rectangle();
    if (!rect) return "";
    const lignes = [];
    for (let r = rect.rMin; r <= rect.rMax; r++) {
      const ligne = ctx.ligneParDispIndex[r];
      const cols = [];
      for (let c = rect.cMin; c <= rect.cMax; c++) {
        let t = texteBrut(ctx.champs[c], ligne);
        if (/[\t\n"]/.test(t)) t = '"' + t.replace(/"/g, '""') + '"';
        cols.push(t);
      }
      lignes.push(cols.join("\t"));
    }
    return lignes.join("\r\n");
  }

  // ── Coller (Ctrl+V) : format tabulé d'Excel (guillemets et retours à la ligne dans les cellules) ──
  function analyserTSV(texte) {
    const lignes = [];
    let ligne = [], champ = "", guillemets = false;
    texte = texte.replace(/\r\n?/g, "\n");
    for (let i = 0; i < texte.length; i++) {
      const ch = texte[i];
      if (guillemets) {
        if (ch === '"') {
          if (texte[i + 1] === '"') { champ += '"'; i++; } else guillemets = false;
        } else champ += ch;
      } else if (ch === '"' && champ === "") guillemets = true;
      else if (ch === "\t") { ligne.push(champ); champ = ""; }
      else if (ch === "\n") { ligne.push(champ); lignes.push(ligne); ligne = []; champ = ""; }
      else champ += ch;
    }
    if (champ !== "" || ligne.length) { ligne.push(champ); lignes.push(ligne); }
    return lignes;
  }

  async function coller(texte) {
    if (!ctx) return;
    const b = bornes();
    const rect = rectangle() || { rMin: b.rMin, cMin: 0, rMax: b.rMin, cMax: 0 };
    let donnees = analyserTSV(texte);
    if (!donnees.length) return;
    // Une seule valeur collée sur une sélection de plusieurs cellules : elle est recopiée partout (Excel).
    if (donnees.length === 1 && donnees[0].length === 1 && (rect.rMax > rect.rMin || rect.cMax > rect.cMin)) {
      donnees = Array.from({ length: rect.rMax - rect.rMin + 1 }, () => Array(rect.cMax - rect.cMin + 1).fill(donnees[0][0]));
    }
    const existantes = [], nouvelles = [];
    let ignorees = 0;
    donnees.forEach((valeurs, i) => {
      const r = rect.rMin + i;
      const corps = {};
      valeurs.forEach((brut, j) => {
        const champ = ctx.champs[rect.cMin + j];
        if (!champ) { ignorees++; return; }
        if (!editable(champ)) { ignorees++; return; }
        corps[champ.slug] = normaliser(champ, brut);
      });
      if (!Object.keys(corps).length) return;
      const ligne = ctx.ligneParDispIndex[r];
      if (ligne) existantes.push({ ligne, corps });
      else nouvelles.push(corps);
    });
    if (nouvelles.length && ctx.vueReordonnee) {
      notifierErreur(tj("engine/static/engine/js/grille_tableur.js:309:23", {v1: nouvelles.length}));
      nouvelles.length = 0;
    }
    if (nouvelles.length && !await GY.confirmer(tj("engine/static/engine/js/grille_tableur.js:312:39", {v1: nouvelles.length}))) {
      nouvelles.length = 0;
    }
    if (!existantes.length && !nouvelles.length) return;
    const operations = [];
    let echecs = 0;
    for (const { ligne, corps } of existantes) {
      const anciennes = {};
      for (const k of Object.keys(corps)) anciennes[k] = ligne[k] ?? "";
      try {
        await api(`/api/tables/${ctx.table.id}/lignes/${ligne._id}/modifier/`, { method: "POST", body: JSON.stringify(corps) });
        operations.push({ ligneId: ligne._id, valeurs: anciennes });
      } catch (e) { echecs++; }
    }
    for (const corps of nouvelles) {
      try {
        const res = await api(`/api/tables/${ctx.table.id}/lignes/`, {
          method: "POST", body: JSON.stringify(corps),
          headers: { "X-Idempotency-Key": tj("engine/static/engine/js/grille_tableur.js:330:44", {v1: Date.now(), v2: Math.random().toString(36).slice(2)}) },
        });
        if (res && res.ligne && res.ligne._id) operations.push({ supprimerId: res.ligne._id });
      } catch (e) { echecs++; }
    }
    memoriser(ctx.table, ctx.slug, operations, tj("engine/static/engine/js/grille_tableur.js:lbl_collage"));
    notifierSucces(tj("engine/static/engine/js/grille_tableur.js:336:21", {v1: operations.length})
      + tj("engine/static/engine/js/grille_tableur.js:337:10", {v1: echecs ? tj("engine/static/engine/js/grille_tableur.js:337:22", {v1: echecs}) : "", v2: ignorees ? tj("engine/static/engine/js/grille_tableur.js:337:64", {v1: ignorees}) : ""}));
    afficherTable(ctx.slug);
    if (nouvelles.length && typeof rafraichirTables === "function") rafraichirTables();
  }

  // ── Clavier ──────────────────────────────────────────────────────────────────────────────────
  function surTouche(ev) {
    if (!ctx || ev.target !== conteneur()) return;
    const ctrl = ev.ctrlKey || ev.metaKey;
    const fleches = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
    if (fleches[ev.key]) {
      ev.preventDefault();
      deplacer(fleches[ev.key][0], fleches[ev.key][1], ev.shiftKey, ctrl);
      return;
    }
    if (ev.key === "Tab") { ev.preventDefault(); deplacer(0, ev.shiftKey ? -1 : 1); return; }
    if (ev.key === "Home" || ev.key === "End") { ev.preventDefault(); deplacer(ctrl ? (ev.key === "Home" ? -1 : 1) : 0, ev.key === "Home" ? -1 : 1, ev.shiftKey, true); return; }
    if (ctrl && ev.key.toLowerCase() === "z" && ev.shiftKey) { ev.preventDefault(); refaire(); return; }   // Ctrl+Maj+Z = rétablir
    if (ctrl && ev.key.toLowerCase() === "y") { ev.preventDefault(); refaire(); return; }
    if (ctrl && ev.key.toLowerCase() === "z") { ev.preventDefault(); annuler(); return; }
    if (ctrl && ev.key.toLowerCase() === "a") {
      ev.preventDefault();
      const b = bornes();
      if (b.rMax >= b.rMin) selectionner(b.rMin, 0, b.rMax, b.cMax);
      return;
    }
    if (ctrl) return;  // Ctrl+C / Ctrl+V : événements copy / paste ci-dessous
    const rect = rectangle();
    if (!rect) {
      if (ev.key === "Enter") { ev.preventDefault(); deplacer(0, 0); }
      return;
    }
    const td = cellule(_selection.r1, _selection.c1);
    if (ev.key === "Escape") { ev.preventDefault(); _selection = null; _rafraichirSurbrillance(ctx.grille); _fermerBarreFusion(); return; }
    if (ev.key === "Delete" || ev.key === "Backspace") { ev.preventDefault(); viderSelection(); return; }
    if (td && td.dataset.fusionId && (ev.key === "Enter" || ev.key === "F2" || (ev.key.length === 1 && !ev.altKey))) {
      ev.preventDefault();  // cellule fusionnée : on écrit dedans (texte, valeur ou =formule)
      _editerCelluleFusionnee(td, ctx.table, ctx.slug, ev.key.length === 1 ? ev.key : undefined);
      return;
    }
    if (!td || !td.classList.contains("cellule-editable")) {
      if (ev.key === "Enter") { ev.preventDefault(); deplacer(ev.shiftKey ? -1 : 1, 0); }
      return;
    }
    if (ev.key === "Enter" || ev.key === "F2") {
      ev.preventDefault();
      _demarrerEditionCellule(td, ctx.table, ctx.slug, ctx.champs, ctx.ligneParDispIndex);
      return;
    }
    if (ev.key.length === 1 && !ev.altKey) {  // frappe directe : remplace le contenu, comme Excel
      ev.preventDefault();
      _demarrerEditionCellule(td, ctx.table, ctx.slug, ctx.champs, ctx.ligneParDispIndex, ev.key);
    }
  }

  document.addEventListener("copy", (ev) => {
    if (!ctx || document.activeElement !== conteneur() || !rectangle()) return;
    ev.preventDefault();
    ev.clipboardData.setData("text/plain", texteSelection());
    notifierSucces(tj("engine/static/engine/js/grille_tableur.js:396:21"));
  });
  document.addEventListener("paste", (ev) => {
    if (!ctx || document.activeElement !== conteneur()) return;
    const texte = ev.clipboardData && ev.clipboardData.getData("text/plain");
    if (!texte) return;
    ev.preventDefault();
    coller(texte);
  });

  function brancher(nouveau) {
    ctx = nouveau;
    const el = conteneur();
    if (!el) return;
    el.addEventListener("keydown", surTouche);  // conteneur recréé à chaque affichage : pas d'accumulation
    // La grille est reconstruite après un collage, un tri, une annulation… : le focus retombait sur <body>
    // et les raccourcis (Ctrl+Z juste après un Ctrl+V) ne répondaient plus. On le rend à la nouvelle grille.
    el.addEventListener("focusin", () => { grilleActive = true; });
    el.addEventListener("focusout", (e) => { if (e.relatedTarget && !el.contains(e.relatedTarget)) grilleActive = false; });
    if (grilleActive && (!document.activeElement || document.activeElement === document.body)) el.focus({ preventScroll: true });
    // En-têtes figés : la ligne de filtres se place juste sous la ligne de titres.
    const premiere = el.querySelector("thead tr:first-child");
    const filtres = el.querySelector("thead tr.ligne-filtres");
    barreTactile(el, ["etendre", "ligne", "colonne"]);
    if (premiere) el.style.setProperty("--h-entete", premiere.getBoundingClientRect().height + "px");
  }

  window.addEventListener("gy-sous-onglet", () => { const el = conteneur(); if (el) barreTactile(el, ["etendre", "ligne", "colonne"]); });   // la barre d'outils (et ses ↶ ↷) passe au premier plan ou en arrière-plan selon le sous-onglet

  // ── Tactile : les gestes qui exigent Maj/Ctrl deviennent des boutons à effet unique (« Étendre », « Ligne », « Colonne », « Ajouter ») ──
  // Barre insérée au-dessus de la grille, pour le doigt ET la souris (tout se fait sans clavier). Le bouton armé sert au PROCHAIN toucher
  // (gyTactile.prendre(mode) le consomme) ; les autres boutons envoient la touche au clavier virtuel absent (Entrée = modifier, Suppr, annuler).
  let modeTactile = null;
  const majBarreTactile = () => document.querySelectorAll(".gy-tactile [data-mode]").forEach(b => b.classList.toggle("actif", b.dataset.mode === modeTactile));
  function barreTactile(avant, modes) {
    if (!avant) return;
    if (avant.parentElement) avant.parentElement.querySelectorAll(":scope > .gy-tactile").forEach(b => b.remove());   // seulement la barre de CE conteneur (grille et sélecteur de cible coexistent)
    // Les raccourcis clavier de la grille, un bouton chacun (écran tactile, ou souris sans clavier) : mêmes fonctions que Ctrl+Z / Ctrl+Y / Ctrl+A / Ctrl+C / Ctrl+V / Suppr / Échap / F2.
    const touche = (init) => ctx && conteneur().dispatchEvent(new KeyboardEvent("keydown", Object.assign({ bubbles: true }, init)));
    const presseIndispo = () => notifierErreur(tj("engine/static/engine/js/grille_tableur.js:tactile:indispo"));
    const actions = modes.includes("etendre") ? {
      modifier: { nom: tj("engine/static/engine/js/grille_tableur.js:tactile:modifier"), fn: () => touche({ key: "F2" }) },
      vider: { nom: tj("engine/static/engine/js/grille_tableur.js:tactile:vider"), fn: viderSelection },
      annuler: { nom: tj("engine/static/engine/js/grille_tableur.js:tactile:annuler"), fn: annuler },
      refaire: { nom: tj("engine/static/engine/js/grille_tableur.js:tactile:refaire"), fn: refaire },
      tout: { nom: tj("engine/static/engine/js/grille_tableur.js:tactile:tout"), fn: () => touche({ key: "a", ctrlKey: true }) },
      copier: { nom: tj("engine/static/engine/js/grille_tableur.js:tactile:copier"), fn: () => {
        if (!ctx || !rectangle()) return;
        (navigator.clipboard && navigator.clipboard.writeText ? navigator.clipboard.writeText(texteSelection()) : Promise.reject())
          .then(() => notifierSucces(tj("engine/static/engine/js/grille_tableur.js:396:21")), presseIndispo);
      } },
      coller: { nom: tj("engine/static/engine/js/grille_tableur.js:tactile:coller"), fn: () => {
        if (!ctx) return;
        (navigator.clipboard && navigator.clipboard.readText ? navigator.clipboard.readText() : Promise.reject())
          .then((t) => { if (t) coller(t); }, presseIndispo);
      } },
      deselectionner: { nom: tj("engine/static/engine/js/grille_tableur.js:tactile:deselectionner"), fn: () => touche({ key: "Escape" }) },
    } : {};
    // CORRECTIF (2026-10-12) : plus de doublon Annuler/Rétablir. La barre d'outils de la table (#btn-annuler-grille / #btn-refaire-grille)
    // les porte déjà, avec l'état « grisé » et les libellés du serveur : la barre tactile ne les remet que s'ils sont absents (sélecteur de cible…).
    const _bAnn = document.getElementById("btn-annuler-grille");
    if (_bAnn && !_bAnn.closest("[hidden]")) { delete actions.annuler; delete actions.refaire; }   // visibles dans la barre d'outils : pas de doublon ; dans un autre sous-onglet (caché), la barre tactile les reprend
    const noms = { etendre: tj("engine/static/engine/js/grille_tableur.js:432:30"), ligne: tj("engine/static/engine/js/grille_tableur.js:432:48"), colonne: tj("engine/static/engine/js/grille_tableur.js:432:66"), ajouter: tj("engine/static/engine/js/grille_tableur.js:432:86") };
    const div = document.createElement("div");
    div.className = "gy-tactile";
    div.innerHTML = modes.map(m => `<button type="button" data-mode="${m}">${noms[m]}</button>`).join("") + Object.keys(actions).map(a => `<button type="button" data-act="${a}">${actions[a].nom}</button>`).join("");
    div.addEventListener("click", (ev) => {
      const b = ev.target.closest("button");
      if (!b) return;
      if (b.dataset.mode) { modeTactile = modeTactile === b.dataset.mode ? null : b.dataset.mode; majBarreTactile(); }
      else actions[b.dataset.act].fn();
    });
    avant.before(div);
    majBarreTactile();
    fabTactile();
  }
  // Bouton flottant : les boutons de la table sont masqués par défaut ; il les affiche dans un popup (re-clic ou clic ailleurs = fermer).
  function fabTactile() {
    let fab = document.getElementById("gy-tactile-fab");
    if (!fab) {
      fab = document.createElement("button");
      fab.type = "button"; fab.id = "gy-tactile-fab"; fab.textContent = "☰"; fab.setAttribute("aria-expanded", "false");
      fab.title = tj("engine/static/engine/js/grille_tableur.js:tactile:fab"); fab.setAttribute("aria-label", fab.title);
      const ouvrir = (o) => { document.body.classList.toggle("gy-tactile-ouvert", o); fab.setAttribute("aria-expanded", o ? "true" : "false"); };
      fab.addEventListener("click", () => ouvrir(!document.body.classList.contains("gy-tactile-ouvert")));
      document.addEventListener("click", (ev) => { if (document.body.classList.contains("gy-tactile-ouvert") && !ev.target.closest("#gy-tactile-fab, .gy-tactile")) ouvrir(false); });
      let _raf = 0;
      const maj = () => { _raf = 0; const v = [...document.querySelectorAll(".gy-tactile")].some((d) => !d.closest("[hidden]") && d.parentElement && d.parentElement.getClientRects().length); fab.classList.toggle("visible", v); if (!v) ouvrir(false); };
      window.gyMajFabTactile = () => { if (!_raf) _raf = requestAnimationFrame(maj); };
      new MutationObserver(window.gyMajFabTactile).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["hidden", "style", "class"] });
      document.body.appendChild(fab);
    }
    window.gyMajFabTactile();
  }
  const prendreTactile = (m) => { if (modeTactile !== m) return false; modeTactile = null; majBarreTactile(); return true; };
  window.gyTactile = { prendre: prendreTactile, barre: barreTactile };

  window.gyGrille = { brancher, deplacer, refocaliser, memoriser, memoriserServeur, normaliser, afficherAgregats, annuler, refaire, majBoutons, coller, analyserTSV };
})();
