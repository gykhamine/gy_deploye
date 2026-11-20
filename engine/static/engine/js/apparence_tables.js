// ── Apparence personnalisée : liste des tables (fond + texte) et cartes de l'analyse exploratoire ───────────────────
// Rien n'est modifié côté serveur : les choix sont gardés dans ce navigateur (localStorage), par table et par colonne.
//   GYApparence.styleCarte(tableSlug, colSlug) -> chaîne « style » (variables CSS --sc-*) à poser sur .stat-colonne-carte
//   GYApparence.boutonCarte(colSlug)           -> HTML du bouton d'édition d'une carte (délégation de clic posée ici)
//   GYApparence.boutonTable(slug, div)         -> <button> d'édition à placer dans une entrée de la liste des tables
//   GYApparence.appliquerTable(div, slug)      -> applique le fond et le texte choisis à une entrée de la liste
// Les couleurs sont choisies avec la fenêtre commune (selecteur_couleur.js : nom, hexadécimal ou palette).
(function(){
  if(window.GYApparence) return;
  const K = "engine/static/engine/js/apparence_tables.js:";
  const T = (cle, vars) => window.tj ? window.tj(K + cle, vars) : cle;
  const CLE_TABLES = "gy.apparence.tables.v1", CLE_CARTES = "gy.apparence.cartes.v1";
  const HEX = /^#[0-9a-f]{6}$/i;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

  // ── Stockage ────────────────────────────────────────────────────────────────────────────────────────────────────
  function lire(cle){
    try { const v = JSON.parse(localStorage.getItem(cle) || "{}"); return v && typeof v === "object" && !Array.isArray(v) ? v : {}; }
    catch(e) { return {}; }
  }
  function ecrire(cle, valeur){   // false si le navigateur refuse (quota plein, stockage bloqué)
    try { localStorage.setItem(cle, JSON.stringify(valeur)); return true; }
    catch(e) { return false; }
  }
  const couleurOk = (c) => typeof c === "string" && HEX.test(c) ? c.toLowerCase() : "";
  const imageOk = (u) => typeof u === "string" && /^data:image\/(jpeg|png|webp);base64,[a-z0-9+/=]+$/i.test(u) ? u : "";
  function erreurStockage(){ if(typeof notifierErreur === "function") notifierErreur(T("stockage_plein")); }

  // ── Contraste (WCAG) ────────────────────────────────────────────────────────────────────────────────────────────
  function luminance(hex){
    const v = [1, 3, 5].map(i => { const c = parseInt(hex.slice(i, i + 2), 16) / 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); });
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
  }
  function contraste(a, b){
    const la = luminance(a), lb = luminance(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  }
  const meilleurTexte = (fond) => contraste(fond, "#000000") >= contraste(fond, "#ffffff") ? "#000000" : "#ffffff";   // le plus lisible sur ce fond
  function niveau(ratio){ return ratio >= 7 ? T("contraste_excellent") : ratio >= 4.5 ? T("contraste_bon") : ratio >= 3 ? T("contraste_faible") : T("contraste_insuffisant"); }

  // ── Fenêtre de couleur : un bouton « pastille + valeur » qui ouvre le sélecteur commun ─────────────────────────
  function champCouleur(libelle, valeur, auSelection){
    const ligne = document.createElement("div");
    ligne.style.cssText = "display:flex;align-items:center;gap:8px;margin-top:8px;font-size:13px";
    const lib = document.createElement("span"); lib.style.cssText = "flex:1;min-width:0"; lib.textContent = libelle;
    const bouton = document.createElement("button");
    bouton.type = "button";
    bouton.style.cssText = "display:flex;align-items:center;gap:6px;padding:3px 8px;font-size:12px";
    const pastille = document.createElement("span");
    pastille.style.cssText = "display:inline-block;width:18px;height:18px;border-radius:4px;border:1px solid var(--border,#333)";
    const val = document.createElement("span");
    const maj = (v) => { pastille.style.background = v || "transparent"; val.textContent = v || T("auto"); };
    maj(valeur);
    bouton.append(pastille, val);
    bouton.onclick = async () => {
      if(!window.GYCouleur) return;
      const c = await window.GYCouleur.choisir({titre: libelle, valeur: bouton._v || "", retirable: true, alpha: false});
      if(c === null) return;   // fenêtre fermée sans choix
      bouton._v = couleurOk(c); maj(bouton._v); auSelection(bouton._v);
    };
    bouton._v = valeur;
    ligne.append(lib, bouton);
    return ligne;
  }
  function fenetre(titre){
    document.getElementById("gy-apparence-fond")?.remove();
    const fond = document.createElement("div");
    fond.id = "gy-apparence-fond";
    fond.style.cssText = "position:fixed;inset:0;background:color-mix(in srgb,var(--voile,#000000) 70%,transparent);display:flex;align-items:center;justify-content:center;z-index:4000;padding:16px";
    const boite = document.createElement("div");
    boite.setAttribute("role", "dialog");
    boite.style.cssText = "width:400px;max-width:100%;max-height:92vh;overflow-y:auto;background:var(--panel,#1b1e24);color:var(--text,#eee);border:1px solid var(--border,#333);border-radius:10px;padding:18px";
    const h = document.createElement("h2"); h.style.cssText = "margin:0 0 4px;font-size:15px"; h.textContent = titre;
    boite.append(h); fond.append(boite);
    fond.addEventListener("mousedown", (ev) => { if(ev.target === fond) fond.remove(); });
    document.body.append(fond);
    return {fond, boite};
  }
  function pied(boite, {reinit, enregistrer, fond}){
    const bas = document.createElement("div");
    bas.style.cssText = "display:flex;gap:8px;justify-content:space-between;margin-top:16px";
    const b1 = document.createElement("button"); b1.type = "button"; b1.textContent = T("reinitialiser"); b1.onclick = () => { reinit(); fond.remove(); };
    const d = document.createElement("span"); d.style.cssText = "display:flex;gap:8px";
    const b2 = document.createElement("button"); b2.type = "button"; b2.textContent = T("annuler"); b2.onclick = () => fond.remove();
    const b3 = document.createElement("button"); b3.type = "button"; b3.className = "principal"; b3.textContent = T("enregistrer"); b3.onclick = () => { if(enregistrer() !== false) fond.remove(); };
    d.append(b2, b3); bas.append(b1, d); boite.append(bas);
  }

  // ── Liste des tables : fond de l'entrée + couleur du texte ──────────────────────────────────────────────────────
  function appliquerTable(div, slug){
    const c = lire(CLE_TABLES)[slug] || {};
    const fond = couleurOk(c.fond), texte = couleurOk(c.texte) || (fond ? meilleurTexte(fond) : "");
    if(!fond && !texte){ div.removeAttribute("data-perso"); div.style.removeProperty("--it-fond"); div.style.removeProperty("--it-texte"); return; }
    div.setAttribute("data-perso", "1");
    if(fond) div.style.setProperty("--it-fond", fond); else div.style.removeProperty("--it-fond");
    if(texte) div.style.setProperty("--it-texte", texte); else div.style.removeProperty("--it-texte");
  }
  function editerTable(slug, div){
    const actuel = lire(CLE_TABLES)[slug] || {};
    let fondC = couleurOk(actuel.fond), texteC = couleurOk(actuel.texte);
    const nom = (typeof tables !== "undefined" ? tables : []).find(t => t.slug === slug)?.nom || slug;
    const {fond, boite} = fenetre(T("table_titre", {v1: nom}));
    const aide = document.createElement("p"); aide.style.cssText = "color:var(--muted,#999);margin:0 0 6px;font-size:12px"; aide.textContent = T("table_aide");
    const apercu = document.createElement("div");
    apercu.style.cssText = "margin-top:12px;padding:8px 10px;border:1px solid var(--border,#333);border-radius:6px;font-size:13px";
    const verdict = document.createElement("div"); verdict.style.cssText = "margin-top:6px;font-size:12px;color:var(--muted,#999)";
    const maj = () => {
      const f = fondC, t = texteC || (f ? meilleurTexte(f) : "");
      apercu.style.background = f || "transparent"; apercu.style.color = t || "inherit";
      apercu.textContent = nom;
      const refFond = f || (window.gyCouleurHex ? window.gyCouleurHex("--panel", "#1b1e24") : "#1b1e24");
      const refTexte = t || (window.gyCouleurHex ? window.gyCouleurHex("--text", "#eeeeee") : "#eeeeee");
      const r = contraste(refFond, refTexte);
      verdict.textContent = T("contraste_ratio", {v1: r.toFixed(1), v2: niveau(r)});
    };
    boite.append(aide,
      champCouleur(T("couleur_fond"), fondC, (v) => { fondC = v; maj(); }),
      champCouleur(T("couleur_texte"), texteC, (v) => { texteC = v; maj(); }),
      apercu, verdict);
    maj();
    const apres = () => {
      appliquerTable(div, slug);
      if(typeof rafraichirTables === "function") rafraichirTables();
    };
    pied(boite, {fond,
      reinit: () => { const tout = lire(CLE_TABLES); delete tout[slug]; ecrire(CLE_TABLES, tout); apres(); },
      enregistrer: () => {
        const tout = lire(CLE_TABLES);
        if(fondC || texteC) tout[slug] = {fond: fondC, texte: texteC}; else delete tout[slug];
        if(!ecrire(CLE_TABLES, tout)){ erreurStockage(); return false; }
        apres();
      }});
  }
  function boutonTable(slug, div){
    const b = document.createElement("button");
    b.type = "button"; b.className = "gy-app-table"; b.textContent = T("bouton_aa");
    b.title = T("table_bouton"); b.setAttribute("aria-label", b.title);
    b.onclick = (ev) => { ev.stopPropagation(); editerTable(slug, div); };
    return b;
  }

  // ── Cartes de l'analyse exploratoire ────────────────────────────────────────────────────────────────────────────
  // Réglages d'une carte : fond, nom, type, libelle, valeur (couleurs de texte), carte (bordure + barre), image, voile (0-90 %).
  function styleCarte(tableSlug, colSlug){
    const c = (lire(CLE_CARTES)[tableSlug] || {})[colSlug];
    return c ? calculer(c) : "";
  }
  function boutonCarte(colSlug){
    return `<button type="button" class="gy-app-carte" data-gy-app-col="${esc(colSlug)}" title="${esc(T("carte_bouton"))}" aria-label="${esc(T("carte_bouton"))}">${esc(T("bouton_aa"))}</button>`;
  }
  function rafraichirCartes(tableSlug){
    document.querySelectorAll("#grille-stats-colonnes .stat-colonne-carte[data-col]").forEach(el => {
      const s = styleCarte(tableSlug, el.dataset.col);
      if(s) el.setAttribute("style", s); else el.removeAttribute("style");
    });
  }
  function reduireImage(fichier){   // image choisie -> JPEG de 480 px max (le stockage du navigateur est limité)
    return new Promise((ok, ko) => {
      const lecteur = new FileReader();
      lecteur.onerror = () => ko(new Error("lecture"));
      lecteur.onload = () => {
        const img = new Image();
        img.onerror = () => ko(new Error("image"));
        img.onload = () => {
          const k = Math.min(1, 480 / Math.max(img.width, img.height));
          const cv = document.createElement("canvas");
          cv.width = Math.max(1, Math.round(img.width * k)); cv.height = Math.max(1, Math.round(img.height * k));
          cv.getContext("2d").drawImage(img, 0, 0, cv.width, cv.height);
          ok(cv.toDataURL("image/jpeg", 0.8));
        };
        img.src = lecteur.result;
      };
      lecteur.readAsDataURL(fichier);
    });
  }
  function editerCarte(tableSlug, colSlug, carteEl){
    const colonnes = Array.from(document.querySelectorAll("#grille-stats-colonnes .stat-colonne-carte[data-col]")).map(e => e.dataset.col);
    const nomCol = carteEl.querySelector(".nom-col")?.textContent || colSlug;
    const typeCol = carteEl.querySelector(".type-col")?.textContent || "";
    const etat = Object.assign({fond: "", nom: "", type: "", libelle: "", valeur: "", carte: "", image: "", voile: 60}, (lire(CLE_CARTES)[tableSlug] || {})[colSlug] || {});
    for(const k of ["fond", "nom", "type", "libelle", "valeur", "carte"]) etat[k] = couleurOk(etat[k]);
    etat.image = imageOk(etat.image);
    if(isNaN(Number(etat.voile))) etat.voile = 60;
    const {fond, boite} = fenetre(T("carte_titre", {v1: nomCol}));
    const apercu = document.createElement("div");
    apercu.className = "stat-colonne-carte";
    apercu.style.cssText = "margin-bottom:6px";
    apercu.innerHTML = `<div class="nom-col"></div><div class="type-col"></div>
      <div class="ligne-stat"><span>${esc(T("ex_libelle"))}</span><b>${esc(T("ex_valeur"))}</b></div>
      <div class="barre-remplissage"><div class="barre-remplissage-valeur" style="width:70%"></div></div>`;
    apercu.querySelector(".nom-col").textContent = nomCol; apercu.querySelector(".type-col").textContent = typeCol;
    const verdict = document.createElement("div"); verdict.style.cssText = "font-size:12px;color:var(--muted,#999);margin-bottom:4px";
    const maj = () => {
      apercu.setAttribute("style", "margin-bottom:6px;" + calculer(etat));   // même règle que la vraie carte, sans rien enregistrer
      const f = etat.fond || (window.gyCouleurHex ? window.gyCouleurHex("--fond-noir", "#000000") : "#000000");
      const t = etat.nom || (etat.fond ? meilleurTexte(etat.fond) : (window.gyCouleurHex ? window.gyCouleurHex("--text", "#eeeeee") : "#eeeeee"));
      const r = contraste(f, t);
      verdict.textContent = T("contraste_ratio", {v1: r.toFixed(1), v2: niveau(r)});
    };
    boite.append(apercu, verdict);
    boite.append(
      champCouleur(T("couleur_fond"), etat.fond, (v) => { etat.fond = v; maj(); }),
      champCouleur(T("texte_nom"), etat.nom, (v) => { etat.nom = v; maj(); }),
      champCouleur(T("texte_type"), etat.type, (v) => { etat.type = v; maj(); }),
      champCouleur(T("texte_libelles"), etat.libelle, (v) => { etat.libelle = v; maj(); }),
      champCouleur(T("texte_valeurs"), etat.valeur, (v) => { etat.valeur = v; maj(); }),
      champCouleur(T("couleur_carte"), etat.carte, (v) => { etat.carte = v; maj(); }));
    // Image de fond
    const blocImage = document.createElement("div");
    blocImage.style.cssText = "margin-top:10px;padding-top:10px;border-top:1px solid var(--border,#333);font-size:13px";
    const rangeeImg = document.createElement("div"); rangeeImg.style.cssText = "display:flex;align-items:center;gap:8px";
    const libImg = document.createElement("span"); libImg.style.flex = "1"; libImg.textContent = T("image_fond");
    const fichier = document.createElement("input"); fichier.type = "file"; fichier.accept = "image/*"; fichier.hidden = true;
    const choisir = document.createElement("button"); choisir.type = "button"; choisir.style.cssText = "padding:3px 8px;font-size:12px"; choisir.textContent = T("image_choisir");
    const retirer = document.createElement("button"); retirer.type = "button"; retirer.style.cssText = "padding:3px 8px;font-size:12px"; retirer.textContent = T("image_retirer");
    rangeeImg.append(libImg, choisir, retirer, fichier);
    const rangeeVoile = document.createElement("div"); rangeeVoile.style.cssText = "display:flex;align-items:center;gap:8px;margin-top:8px";
    const libVoile = document.createElement("span"); libVoile.style.flex = "1"; libVoile.textContent = T("image_voile");
    const curseur = document.createElement("input"); curseur.type = "range"; curseur.min = "0"; curseur.max = "90"; curseur.value = String(etat.voile); curseur.setAttribute("data-gy-natif", "1");
    const valVoile = document.createElement("span"); valVoile.style.cssText = "min-width:38px;text-align:right;color:var(--muted,#999)";
    rangeeVoile.append(libVoile, curseur, valVoile);
    const majImage = () => { retirer.hidden = !etat.image; rangeeVoile.style.display = etat.image ? "flex" : "none"; valVoile.textContent = etat.voile + " %"; };
    choisir.onclick = () => fichier.click();
    retirer.onclick = () => { etat.image = ""; majImage(); maj(); };
    fichier.onchange = async () => {
      const f = fichier.files && fichier.files[0]; if(!f) return;
      try { etat.image = await reduireImage(f); } catch(e) { if(typeof notifierErreur === "function") notifierErreur(T("image_illisible")); return; }
      majImage(); maj();
    };
    curseur.oninput = () => { etat.voile = Number(curseur.value); majImage(); maj(); };
    blocImage.append(rangeeImg, rangeeVoile); boite.append(blocImage);
    // Appliquer à toutes les cartes de la table
    const tout = document.createElement("label");
    tout.style.cssText = "display:flex;align-items:center;gap:6px;margin-top:12px;font-size:12px;cursor:pointer";
    const coche = document.createElement("input"); coche.type = "checkbox"; coche.setAttribute("data-gy-natif", "1");
    tout.append(coche, document.createTextNode(T("appliquer_toutes", {v1: colonnes.length}))); boite.append(tout);
    majImage(); maj();
    const cibles = () => coche.checked ? colonnes : [colSlug];
    pied(boite, {fond,
      reinit: () => {
        const toutes = lire(CLE_CARTES); const t = toutes[tableSlug] || {};
        for(const c of cibles()) delete t[c];
        if(Object.keys(t).length) toutes[tableSlug] = t; else delete toutes[tableSlug];
        ecrire(CLE_CARTES, toutes); rafraichirCartes(tableSlug);
      },
      enregistrer: () => {
        const toutes = lire(CLE_CARTES); const t = toutes[tableSlug] || {};
        const vide = !etat.fond && !etat.nom && !etat.type && !etat.libelle && !etat.valeur && !etat.carte && !etat.image;
        for(const c of cibles()){ if(vide) delete t[c]; else t[c] = Object.assign({}, etat); }
        if(Object.keys(t).length) toutes[tableSlug] = t; else delete toutes[tableSlug];
        if(!ecrire(CLE_CARTES, toutes)){ erreurStockage(); return false; }
        rafraichirCartes(tableSlug);
      }});
  }
  function calculer(c){   // réglages d'une carte -> variables CSS --sc-* ; sans texte choisi, le texte prend le meilleur contraste sur le fond
    const fond = couleurOk(c.fond), auto = fond ? meilleurTexte(fond) : "";
    const v = [];
    if(fond) v.push("--sc-fond:" + fond);
    for(const [k, nomVar] of [["nom", "--sc-nom"], ["type", "--sc-type"], ["libelle", "--sc-libelle"], ["valeur", "--sc-valeur"]]){
      const x = couleurOk(c[k]) || auto; if(x) v.push(nomVar + ":" + x);
    }
    const bord = couleurOk(c.carte); if(bord) v.push("--sc-bord:" + bord);
    const img = imageOk(c.image);
    if(img){
      const a = Math.min(Math.max(Number(c.voile), 0), 90) / 100;
      const base = fond || "#000000";
      const rgba = "rgba(" + [1, 3, 5].map(i => parseInt(base.slice(i, i + 2), 16)).join(",") + "," + (isNaN(a) ? 0.6 : a) + ")";
      v.push('--sc-image:linear-gradient(' + rgba + ',' + rgba + '),url("' + img + '")');
    }
    return v.join(";");
  }

  // Clic sur le bouton d'une carte (les cartes sont recréées à chaque affichage de la table : délégation).
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest && ev.target.closest(".gy-app-carte");
    if(!b) return;
    ev.preventDefault(); ev.stopPropagation();   // ne referme pas l'en-tête repliable
    const carte = b.closest(".stat-colonne-carte");
    const table = carte && carte.parentElement ? carte.parentElement.dataset.table || "" : "";
    if(carte && table) editerCarte(table, b.dataset.gyAppCol, carte);
  });

  window.GYApparence = {styleCarte, boutonCarte, boutonTable, appliquerTable};
})();
