/* Entête officiel (bloc gauche / logo / bloc droite) : aperçu partagé par les pages « Documents administratifs » et
   « Configuration des documents », et éditeur de lignes (UN seul éditeur, deux usages).
   Page de configuration : l'administrateur édite les lignes du modèle d'entête par défaut ; l'agent voit les lignes imposées
   (verrouillées) et complète à la suite. Page des documents : l'administrateur édite un modèle d'entête nommé, via
   `opts.charger` / `opts.sauver` (même éditeur, autre source de données). */
(function(){
  const CSRF = document.cookie.match(/gy_csrftoken=([^;]+)/)?.[1] || "";
  const ALIGN = {centre:"center", gauche:"left", droite:"right"};
  const esc = s => String(s == null ? "" : s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");

  function injecterStyle(){
    if (document.getElementById("eo-style")) return;
    const st = document.createElement("style");
    st.id = "eo-style";
    st.textContent = `
      .eo-apercu{display:grid;grid-template-columns:1fr auto 1fr;gap:10px;align-items:start;background:var(--fond-clair,#fff);color:var(--texte-document,#000);border:1px solid var(--border);border-radius:8px;padding:12px;font:11px/1.35 Arial,Helvetica,sans-serif;min-height:60px}
      .eo-apercu .eo-centre{width:54px;height:54px;display:flex;align-items:center;justify-content:center;font-size:26px;opacity:.55;align-self:center}
      .eo-apercu .eo-centre img{max-width:100%;max-height:100%;object-fit:contain}
      .eo-apercu .eo-vide{opacity:.35;font-style:italic}
      .eo-grille{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(300px,100%),1fr));gap:14px;margin-bottom:14px}
      .eo-colonne{border:1px solid var(--border);border-radius:8px;padding:12px 14px;background:var(--fond-sombre,#0a0d10)}
      .eo-colonne h3{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-bottom:8px}
      .eo-impose{display:flex;flex-direction:column;gap:4px;margin-bottom:8px}
      .eo-impose .eo-ligne-fixe{font-size:12px;color:var(--muted);padding:5px 8px;border:1px dashed var(--border);border-radius:6px}
      .eo-lignes{display:flex;flex-direction:column;gap:6px;margin-bottom:8px}
      .eo-ligne{display:flex;align-items:center;gap:4px;flex-wrap:wrap}
      .eo-ligne input[type=text]{flex:1;min-width:140px;background:var(--fond-noir,#000);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:6px 8px;font:inherit;font-size:12px}
      .eo-ligne select{background:var(--fond-noir,#000);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:5px 4px;font:inherit;font-size:12px}
      .eo-ligne button{padding:3px 8px !important;font-size:12px !important}
      .eo-numero{margin-top:12px;padding-top:10px;border-top:1px dashed var(--border);display:flex;flex-direction:column;gap:6px}
      .eo-numero label{display:flex;gap:6px;align-items:flex-start;font-size:12px;cursor:pointer}
      .eo-numero input[type=text]{width:100%;background:var(--fond-noir,#000);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:6px 8px;font:inherit;font-size:12px}
      .eo-numero .eo-num-aide{font-size:11px;color:var(--muted);margin:0}
      .eo-ligne button.eo-actif{background:var(--accent) !important;color:var(--texte-accent,#000) !important;border-color:var(--accent) !important}
    `;
    document.head.appendChild(st);
  }

  function nettoyer(lignes){
    return (lignes || []).filter(l => l && String(l.texte || "").trim()).slice(0, 12);
  }
  function styleLigne(l){
    return `font-weight:${l.gras ? 700 : 400};font-style:${l.italique ? "italic" : "normal"};text-decoration:${l.souligne ? "underline" : "none"};text-align:${ALIGN[l.align] || "center"}`;
  }
  function blocHtml(lignes){
    const l = nettoyer(lignes);
    return `<div>${l.length ? l.map(x => `<div style="${styleLigne(x)}">${esc(x.texte)}</div>`).join("") : `<div class="eo-vide">…</div>`}</div>`;
  }

  /* Aperçu : [bloc gauche | logo | bloc droite], comme sur le PDF. */
  function monterApercu(el, gauche, droite, logoUrl, numero){
    injecterStyle();
    // Le numéro de document est toujours la DERNIÈRE ligne du bloc gauche (comme sur le PDF).
    if (numero) gauche = nettoyer(gauche).concat([{texte: numero, gras: true, italique: false, souligne: false, align: "centre"}]);
    el.innerHTML = `<div class="eo-apercu">${blocHtml(gauche)}<div class="eo-centre">${logoUrl ? `<img src="${esc(logoUrl)}" alt="">` : "🏢"}</div>${blocHtml(droite)}</div>`;
  }

  async function appel(url, opts){
    const res = await fetch(url, {headers:{"Content-Type":"application/json","X-CSRFToken":CSRF}, ...(opts || {})});
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.erreur || String(res.status));
    return data;
  }
  const notifier = (msg, err) => { if (typeof window.toast === "function") window.toast(msg, !!err); };

  /* Éditeur de lignes. `opts` : {racine, logoUrl, charger?, sauver?, sansAide?}. Par défaut : /api/entete-officiel/.
     `charger()` renvoie {est_agent, imposees:{gauche,droite}, perso, limites} ; `sauver({gauche, droite})` renvoie la même forme. */
  async function monterEditeur(opts){
    injecterStyle();
    const racine = opts.racine;
    const charger = opts.charger || (() => appel("/api/entete-officiel/"));
    const sauver = opts.sauver || (charge => appel("/api/entete-officiel/", {method: "POST", body: JSON.stringify(charge)}));
    let EN;
    try { EN = await charger(); }
    catch(e){ racine.innerHTML = `<p class="aide">${esc(e.message)}</p>`; return; }
    const editable = {gauche: [], droite: []};
    const source = EN.est_agent ? EN.perso : EN.imposees;
    ["gauche", "droite"].forEach(c => { editable[c] = (source[c] || []).map(l => ({...l})); });

    racine.innerHTML = `
      ${opts.sansAide ? "" : `<p class="aide" style="margin:0 0 12px">${EN.est_agent
        ? tj("engine/static/engine/js/entete_officiel.js:aide_agent")
        : tj("engine/static/engine/js/entete_officiel.js:aide_admin")}</p>`}
      <div class="eo-grille">
        ${["gauche", "droite"].map(c => `
          <div class="eo-colonne" data-cote="${c}">
            <h3>${c === "gauche" ? tj("engine/static/engine/js/entete_officiel.js:titre_gauche") : tj("engine/static/engine/js/entete_officiel.js:titre_droite")}</h3>
            <div class="eo-impose"></div>
            <div class="eo-lignes"></div>
            <button type="button" class="petit eo-ajouter">${tj("engine/static/engine/js/entete_officiel.js:ajouter_ligne")}</button>
            ${c === "gauche" && EN.reglages ? `
            <div class="eo-numero">
              <label><input type="checkbox" class="eo-num-active"> <span><b>${tj("engine/static/engine/js/entete_officiel.js:num_activer")}</b></span></label>
              <input type="text" class="eo-num-modele" maxlength="${EN.limites.texte}" placeholder="${esc(tj("engine/static/engine/js/entete_officiel.js:num_ph"))}">
              <label><input type="checkbox" class="eo-num-auto"> <span>${tj("engine/static/engine/js/entete_officiel.js:num_auto")}</span></label>
              <p class="eo-num-aide">${tj("engine/static/engine/js/entete_officiel.js:num_aide")}</p>
              <p class="eo-num-aide eo-num-exemple"></p>
            </div>` : ""}
          </div>`).join("")}
      </div>
      <div class="eo-apercu-zone" style="margin-bottom:14px"></div>
      <button type="button" class="principal eo-enregistrer">${tj("engine/static/engine/js/entete_officiel.js:enregistrer")}</button>`;

    const zoneApercu = racine.querySelector(".eo-apercu-zone");
    function fusionne(c){
      return (EN.est_agent ? (EN.imposees[c] || []) : []).concat(editable[c]);
    }
    // Numérotation (réglage personnel, seulement quand l'API le fournit) : texte libre ; avec l'aide automatique, {n} et {année} sont remplacés.
    let reg = EN.reglages ? {...EN.reglages} : null;
    function exemple(){
      if (!reg || !reg.numerotation_active || !String(reg.numerotation_modele || "").trim()) return "";
      const texte = reg.numerotation_modele.trim();
      if (!reg.numerotation_auto) return texte;
      const rempl = (n, spec) => { const m = /^(0?)(\d{1,2})d$/.exec(spec || ""); return m ? String(n).padStart(+m[2], m[1] ? "0" : " ") : String(n); };
      return texte.replace(/\{n(?::([^}]*))?\}/g, (_, sp) => rempl((reg.dernier || 0) + 1, sp))
                  .replace(/\{ann(?:e|é)e(?::([^}]*))?\}/g, (_, sp) => rempl(new Date().getFullYear(), sp))
                  .replace(/\{type\}/g, "courrier");
    }
    function majApercu(){
      monterApercu(zoneApercu, fusionne("gauche"), fusionne("droite"), opts.logoUrl, exemple());
      const ex = racine.querySelector(".eo-num-exemple");
      if (ex) ex.textContent = exemple() ? tj("engine/static/engine/js/entete_officiel.js:num_exemple", {v1: exemple()}) : "";
    }

    function rendreCote(c){
      const col = racine.querySelector(`.eo-colonne[data-cote="${c}"]`);
      const imp = col.querySelector(".eo-impose");
      imp.innerHTML = EN.est_agent ? nettoyer(EN.imposees[c]).map(l => `<div class="eo-ligne-fixe" style="${styleLigne(l)}">🔒 ${esc(l.texte)}</div>`).join("") : "";
      const zone = col.querySelector(".eo-lignes");
      zone.innerHTML = "";
      editable[c].forEach((l, i) => {
        const ligne = document.createElement("div");
        ligne.className = "eo-ligne";
        ligne.innerHTML = `
          <input type="text" maxlength="${EN.limites.texte}" value="${esc(l.texte)}" placeholder="${tj("engine/static/engine/js/entete_officiel.js:ph_ligne")}">
          <button type="button" data-a="gras" title="${tj("engine/static/engine/js/entete_officiel.js:t_gras")}"><b>B</b></button>
          <button type="button" data-a="italique" title="${tj("engine/static/engine/js/entete_officiel.js:t_italique")}"><i>I</i></button>
          <button type="button" data-a="souligne" title="${tj("engine/static/engine/js/entete_officiel.js:t_souligne")}"><u>U</u></button>
          <select data-a="align">
            <option value="centre">${tj("engine/static/engine/js/entete_officiel.js:al_centre")}</option>
            <option value="gauche">${tj("engine/static/engine/js/entete_officiel.js:al_gauche")}</option>
            <option value="droite">${tj("engine/static/engine/js/entete_officiel.js:al_droite")}</option>
          </select>
          <button type="button" data-a="haut">↑</button>
          <button type="button" data-a="bas">↓</button>
          <button type="button" class="danger" data-a="suppr">✕</button>`;
        ligne.querySelector("select").value = l.align || "centre";
        ["gras", "italique", "souligne"].forEach(k => ligne.querySelector(`[data-a="${k}"]`).classList.toggle("eo-actif", !!l[k]));
        ligne.querySelector("input").oninput = ev => { l.texte = ev.target.value; majApercu(); };
        ligne.querySelector("select").onchange = ev => { l.align = ev.target.value; majApercu(); };
        ligne.querySelectorAll("button").forEach(b => b.onclick = () => {
          const a = b.dataset.a;
          if (a === "gras" || a === "italique" || a === "souligne"){ l[a] = !l[a]; b.classList.toggle("eo-actif", !!l[a]); }
          else if (a === "haut" && i > 0){ [editable[c][i-1], editable[c][i]] = [editable[c][i], editable[c][i-1]]; rendreCote(c); }
          else if (a === "bas" && i < editable[c].length - 1){ [editable[c][i+1], editable[c][i]] = [editable[c][i], editable[c][i+1]]; rendreCote(c); }
          else if (a === "suppr"){ editable[c].splice(i, 1); rendreCote(c); }
          majApercu();
        });
        zone.appendChild(ligne);
      });
    }

    racine.querySelectorAll(".eo-ajouter").forEach(b => b.onclick = () => {
      const c = b.closest(".eo-colonne").dataset.cote;
      if (editable[c].length >= EN.limites.lignes){ notifier(tj("engine/static/engine/js/entete_officiel.js:limite"), true); return; }
      editable[c].push({texte: "", gras: false, italique: false, souligne: false, align: "centre"});
      rendreCote(c);
      const champs = racine.querySelectorAll(`.eo-colonne[data-cote="${c}"] .eo-ligne input[type=text]`);
      if (champs.length) champs[champs.length - 1].focus();
    });
    if (reg){
      const ca = racine.querySelector(".eo-num-active"), cm = racine.querySelector(".eo-num-modele"), cx = racine.querySelector(".eo-num-auto");
      const afficherReg = () => { ca.checked = !!reg.numerotation_active; cm.value = reg.numerotation_modele || ""; cx.checked = !!reg.numerotation_auto; };
      afficherReg();
      ca.onchange = () => { reg.numerotation_active = ca.checked; majApercu(); };
      cm.oninput = () => { reg.numerotation_modele = cm.value; majApercu(); };
      cx.onchange = () => { reg.numerotation_auto = cx.checked; majApercu(); };
      racine._afficherReg = afficherReg;
    }
    racine.querySelector(".eo-enregistrer").onclick = async () => {
      try {
        const charge = {gauche: nettoyer(editable.gauche), droite: nettoyer(editable.droite)};
        if (reg) charge.reglages = {numerotation_active: reg.numerotation_active, numerotation_modele: reg.numerotation_modele, numerotation_auto: reg.numerotation_auto};
        const res = await sauver(charge);
        if (res.reglages){ reg = {...res.reglages}; if (racine._afficherReg) racine._afficherReg(); }
        ["gauche", "droite"].forEach(c => { editable[c] = ((res.est_agent ? res.perso : res.imposees)[c] || []).map(l => ({...l})); });
        EN = res;
        ["gauche", "droite"].forEach(rendreCote); majApercu();
        notifier(tj("engine/static/engine/js/entete_officiel.js:enregistre"));
        if (opts.apresSauvegarde) opts.apresSauvegarde(res);
      } catch(e){ notifier(e.message, true); }
    };
    ["gauche", "droite"].forEach(rendreCote);
    majApercu();
  }

  window.EnteteOfficiel = {monterApercu, monterEditeur, appel};
})();
