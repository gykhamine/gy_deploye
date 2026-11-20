// ── Sélecteur de couleur partagé ─────────────────────────────────────────────────────────────────────────────────
// UNE seule fenêtre de choix de couleur pour tout agentgy : un NOM (bleu, bordeaux…), un code HEXADÉCIMAL (#1e90ff) ou la PALETTE.
//   GYCouleur.choisir({titre, valeur, retirable, alpha}) -> Promise : « #rrggbb » ou, si l'opacité a été réglée, « #rrggbbaa » (RGBA) ;
//   "" si « Retirer la couleur », null si fermé. alpha:false retire le curseur d'opacité (consommateur qui ne lit que #rrggbb).
//   Sans curseur activé, la couleur reste « #rrggbb » (teinte standard de la grille et du PDF) ; avec, l'opacité choisie est appliquée partout.
//   Tout <input type="color"> (présent ou créé plus tard) ouvre cette fenêtre au clic ; sa .value reçoit la couleur et les
//   évènements input/change partent comme avec le sélecteur du navigateur : le code existant n'a rien à changer.
// Palette, noms et charte de l'entreprise (« Ma charte ») : GET /api/couleurs-colonne/ (engine/couleur_colonne.py, même règle que le PDF).
(function(){
  let _pal = null;
  async function palette(){
    if(!_pal){ const r = await fetch("/api/couleurs-colonne/", {credentials: "same-origin"}); _pal = await r.json(); }
    return _pal;
  }
  function texteSur(hex){   // même règle que couleur_colonne.couleur_texte : texte noir ou blanc selon le fond
    const a = hex.length === 9 ? parseInt(hex.slice(7, 9), 16) / 255 : 1;
    if(a < 0.6) return "inherit";   // fond très transparent : on voit surtout la page, le texte garde la couleur du thème
    const v = [1, 3, 5].map(i => 255 - (255 - parseInt(hex.slice(i, i + 2), 16)) * a);
    return (0.299 * v[0] + 0.587 * v[1] + 0.114 * v[2]) > 150 ? gyCouleur("--fond-sombre","#111111") : gyCouleur("--texte-sombre","#ffffff");
  }
  function resoudre(texte, noms){   // nom, hexadécimal (3, 4, 6, 8 chiffres) ou rgba() -> « #rrggbb » / « #rrggbbaa » ; "" = vide ; null = non reconnu
    const brut = (texte || "").trim();
    if(!brut) return "";
    const nom = brut.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[-_]/g, " ").replace(/\s+/g, " ");
    if(noms[nom]) return noms[nom];
    const r = /^rgba?\(\s*(\d{1,3})\s*[,\s]\s*(\d{1,3})\s*[,\s]\s*(\d{1,3})\s*(?:[,/]\s*([0-9]*\.?[0-9]+%?)\s*)?\)$/i.exec(brut);
    if(r){
      const v = [r[1], r[2], r[3]].map(Number);
      if(Math.max(...v) > 255) return null;
      let c = v.map(x => x.toString(16).padStart(2, "0")).join("");
      if(r[4] !== undefined){ const a = r[4].endsWith("%") ? parseFloat(r[4]) / 100 : parseFloat(r[4]); c += Math.round(Math.min(Math.max(a, 0), 1) * 255).toString(16).padStart(2, "0"); }
      return "#" + c;
    }
    const m = /^#?([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.exec(brut);
    if(!m) return null;
    const c = m[1].toLowerCase();
    return "#" + (c.length <= 4 ? [...c].map(x => x + x).join("") : c);
  }
  const esc = (s) => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

  async function choisir(opts){
    opts = opts || {};
    let pal;
    try { pal = await palette(); } catch(err) { return null; }
    document.getElementById("modal-couleur-fond")?.remove();
    const avecAlpha = opts.alpha !== false;
    const initiale = /^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(opts.valeur || "") ? ((opts.valeur.length === 9 && !avecAlpha) ? opts.valeur.slice(0, 7) : opts.valeur).toLowerCase() : "";
    return new Promise(function(fin){
      const fond = document.createElement("div");
      fond.id = "modal-couleur-fond";
      fond.style.cssText = "position:fixed;inset:0;background:color-mix(in srgb,var(--voile,#000000) 75%,transparent);display:flex;align-items:center;justify-content:center;z-index:5000;padding:16px";
      fond.innerHTML = `
        <div style="width:420px;max-width:100%;max-height:92vh;overflow-y:auto;background:var(--panel,#1b1e24);color:var(--text,#eee);border:1px solid var(--border,#333);border-radius:10px;padding:18px">
          <h2 style="margin:0 0 4px;font-size:15px">${esc(opts.titre || tj("engine/static/engine/js/selecteur_couleur.js:lbl_couleur"))}</h2>
          <p style="color:var(--muted,#999);margin:0 0 12px;font-size:12px">${tj("engine/static/engine/js/selecteur_couleur.js:55:78")}</p>
          <div style="display:flex;gap:8px;align-items:center">
            <input id="couleur-texte" type="text" placeholder="${tj("engine/static/engine/js/selecteur_couleur.js:57:65")}" autocomplete="off" style="flex:1;padding:6px 8px;font-size:13px;box-sizing:border-box">
            <input id="couleur-roue" type="color" data-gy-natif="1" value="${window.gyCouleurHex ? window.gyCouleurHex("--forme-selecteur", "#1e88e5") : "#1e88e5"}" title="${tj("engine/static/engine/js/selecteur_couleur.js:58:93")}" style="width:42px;height:34px;padding:0;border:1px solid var(--border,#333);background:transparent;cursor:pointer">
          </div>
          <div style="display:grid;grid-template-columns:repeat(8,1fr);gap:6px;margin-top:12px">
            ${pal.palette.map(p => `<button type="button" class="couleur-pastille" data-couleur="${p.couleur}" title="${esc(p.nom)}" style="height:28px;border-radius:5px;border:1px solid var(--border,#333);background:${p.couleur};cursor:pointer"></button>`).join("")}
          </div>
          ${(pal.charte || []).length ? `<div style="margin-top:12px;font-size:11px;color:var(--muted,#999)">${tj("engine/static/engine/js/selecteur_couleur.js:63:111")}</div>
          <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:4px">
            ${pal.charte.map(p => `<button type="button" class="couleur-pastille" data-couleur="${p.couleur}" title="${esc(p.nom)} ${p.couleur}" style="height:28px;min-width:28px;padding:0 8px;border-radius:5px;border:1px solid var(--border,#333);background:${p.couleur};color:${texteSur(p.couleur)};font-size:10px;cursor:pointer">${esc(p.nom)}</button>`).join("")}
          </div>` : ""}
          ${avecAlpha ? `<div style="display:flex;align-items:center;gap:8px;margin-top:12px;font-size:12px" title="${tj("engine/static/engine/js/selecteur_couleur.js:67:118")}">
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer"><input id="couleur-alpha-actif" type="checkbox" data-gy-natif="1"> ${tj("engine/static/engine/js/selecteur_couleur.js:68:151")}</label>
            <input id="couleur-alpha" type="range" min="0" max="100" value="100" data-gy-natif="1" disabled style="flex:1">
            <span id="couleur-alpha-val" style="min-width:40px;text-align:right;color:var(--muted,#999)">auto</span>
          </div>` : ""}
          <div style="display:flex;align-items:center;gap:8px;margin-top:12px;font-size:12px">
            <span id="couleur-apercu" style="display:inline-block;min-width:110px;padding:5px 10px;border:1px solid var(--border,#333);border-radius:5px;text-align:center">${esc(opts.apercu || tj("engine/static/engine/js/selecteur_couleur.js:73:195"))}</span>
            <span id="couleur-resume" style="color:var(--muted,#999)"></span>
          </div>
          <div style="display:flex;gap:8px;justify-content:space-between;margin-top:14px">
            <button id="couleur-retirer" type="button" ${opts.retirable === false ? "hidden" : ""}>${tj("engine/static/engine/js/selecteur_couleur.js:77:101")}</button>
            <span style="display:flex;gap:8px"><button id="couleur-fermer" type="button">${tj("engine/static/engine/js/selecteur_couleur.js:78:91")}</button><button id="couleur-appliquer" type="button" class="principal" disabled>${tj("engine/static/engine/js/selecteur_couleur.js:78:178")}</button></span>
          </div>
        </div>`;
      document.body.appendChild(fond);
      const el = (id) => fond.querySelector("#" + id);
      const clore = (v) => { fond.remove(); fin(v); };
      el("couleur-fermer").onclick = () => clore(null);
      fond.addEventListener("mousedown", (ev) => { if(ev.target === fond) clore(null); });
      // État : `choisie` = « #rrggbb » (ou "") ; l'opacité est à part (alphaActif / alphaPct) et ne s'ajoute qu'à la validation.
      let choisie = initiale.slice(0, 7), alphaActif = initiale.length === 9, alphaPct = alphaActif ? Math.round(parseInt(initiale.slice(7, 9), 16) / 2.55) : 100;
      const finale = () => !choisie ? "" : choisie + (alphaActif ? Math.round(alphaPct * 2.55).toString(16).padStart(2, "0") : "");
      const afficher = (reecrireTexte) => {
        const f = finale(), ap = el("couleur-apercu");
        // Damier sous l'aperçu : la transparence se voit.
        ap.style.background = f ? `linear-gradient(${f},${f}), repeating-conic-gradient(var(--texte-discret,#bbb) 0 25%, var(--texte-sombre,#fff) 0 50%) 0 0 / 12px 12px` : "transparent";
        ap.style.color = f ? (texteSur(f) === "inherit" ? gyCouleur("--fond-sombre","#111111") : texteSur(f)) : "inherit";
        if(choisie) el("couleur-roue").value = choisie;
        if(reecrireTexte) el("couleur-texte").value = f;
        const ca = el("couleur-alpha-actif");
        if(ca){ ca.checked = alphaActif; el("couleur-alpha").disabled = !alphaActif || !choisie; el("couleur-alpha").value = alphaPct; el("couleur-alpha-val").textContent = alphaActif ? alphaPct + " %" : "auto"; }
        el("couleur-resume").textContent = f || tj("engine/static/engine/js/selecteur_couleur.js:98:50");
        el("couleur-appliquer").disabled = f === initiale;
      };
      el("couleur-texte").value = initiale;
      el("couleur-texte").addEventListener("input", () => {
        const c = resoudre(el("couleur-texte").value, pal.noms);
        if(c === null){ el("couleur-resume").textContent = tj("engine/static/engine/js/selecteur_couleur.js:104:61"); el("couleur-appliquer").disabled = true; return; }
        choisie = c.slice(0, 7);
        if(c.length === 9 && avecAlpha){ alphaActif = true; alphaPct = Math.round(parseInt(c.slice(7, 9), 16) / 2.55); }
        afficher(false);
      });
      el("couleur-roue").addEventListener("input", (ev) => { choisie = ev.target.value; afficher(true); });
      fond.addEventListener("click", (ev) => {
        const b = ev.target.closest(".couleur-pastille");
        if(b){ choisie = b.dataset.couleur; afficher(true); }
      });
      if(el("couleur-alpha-actif")){
        el("couleur-alpha-actif").addEventListener("change", (ev) => { alphaActif = ev.target.checked; afficher(true); });
        el("couleur-alpha").addEventListener("input", (ev) => { alphaPct = Number(ev.target.value); afficher(true); });
      }
      el("couleur-appliquer").onclick = () => clore(finale());
      el("couleur-retirer").onclick = () => clore("");
      afficher(); el("couleur-texte").focus();
    });
  }

  // Tout <input type="color"> ouvre la fenêtre commune (sauf celui de la fenêtre elle-même : data-gy-natif).
  document.addEventListener("click", async function(ev){
    const inp = ev.target.closest && ev.target.closest('input[type="color"]');
    if(!inp || inp.dataset.gyNatif || inp.disabled) return;
    ev.preventDefault();
    const lib = inp.closest("label")?.textContent.trim() || inp.title || inp.getAttribute("aria-label") || "";
    const c = await choisir({titre: lib ? "Couleur : " + lib : tj("engine/static/engine/js/selecteur_couleur.js:lbl_couleur"), valeur: inp.value, retirable: false, alpha: false});   // un <input type="color"> ne lit que #rrggbb
    if(c){ inp.value = c; inp.dispatchEvent(new Event("input", {bubbles: true})); inp.dispatchEvent(new Event("change", {bubbles: true})); }
  }, true);

  window.GYCouleur = {choisir, resoudre, texteSur, palette};
})();
