// ── Intro animation ──────────────────────────────────────────────────────────
(function(){
  const overlay = document.getElementById("intro-overlay");
  // Arrivée par le bouton « ← Retour à {table} » d'une autre page : pas d'animation d'entrée (elle gênerait), l'adresse est nettoyée.
  if (/[?&]retour=1(&|$)/.test(location.search)){
    overlay.remove();
    history.replaceState(null, "", location.pathname + location.hash);
    return;
  }
  function dismissIntro(){
    overlay.classList.add("sortie");
    setTimeout(() => overlay.remove(), 700);
  }
  // Dismiss après 1.8s ou dès que la page est prête
  if(document.readyState === "complete"){
    setTimeout(dismissIntro, 1800);
  } else {
    window.addEventListener("load", () => setTimeout(dismissIntro, 1800));
  }
})();
// ── Mode Client (Lecteur) ──────────────────────────────────────────────────
function activerModeClient(profil){
  // Masquer la sidebar complète — remplacée par une liste de formulaires
  const sidebar = document.getElementById("sidebar");
  const btnToggle = document.getElementById("btn-toggle-sidebar");
  if(sidebar) sidebar.classList.add("masque");
  if(btnToggle) btnToggle.style.display = "none";

  // Logo entreprise dans l'entête
  const titre = document.getElementById("entete-titre");
  if(titre && profil.logo_entreprise){
    titre.innerHTML = `<img src="${profil.logo_entreprise}" alt="${escapeHtml(profil.entreprise||'')}"
      style="height:32px;max-width:120px;object-fit:contain;border-radius:4px">
      <span style="font-size:14px;font-weight:600">${escapeHtml(profil.entreprise||'')}</span>`;
  } else if(titre && profil.entreprise){
    titre.textContent = profil.entreprise;
  }

  // Remplacer zone-contenu par vue formulaires client
  afficherVueClient();
}
// Champs de chaque table exposée au client, indexés par id de table —
// nécessaire car soumettreFormClient() ne reçoit que le tableId et doit
// pouvoir retrouver la liste des champs (slug, type, obligatoire) pour
// lire correctement les inputs générés par champVersInput().
let _champsFormClient = {};
let _tablesFormClient = {};
async function afficherVueClient(){
  const zone = document.getElementById("zone-contenu");
  const data = await api("/api/tables/");
  const tables = (data.tables || []).filter(t => t.expose_client);
  tables.forEach(t => { _champsFormClient[t.id] = t.champs; });
  if(typeof viderActionsSidebar === "function") viderActionsSidebar();

  if(!tables.length){
    zone.innerHTML = `<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:40vh;gap:12px;color:var(--muted)">
      <div style="font-size:40px">📋</div>
      <div style="font-size:14px">${tj("engine/static/engine/js/index_2.js:53:36")}</div>
    </div>`;
    return;
  }

  zone.innerHTML = tables.map(t => `
    <div class="carte reduite" style="margin-bottom:16px">
      <div class="entete-carte">
        <span class="titre-analyse" style="font-size:15px">${escapeHtml(t.nom)}</span>
        <button class="btn-reduire" title="${tj("engine/static/engine/js/index_2.js:62:45")}">▸</button>
      </div>
      <div class="corps-carte gyf-hote" data-table-id="${t.id}">
        <div class="form-titre">${escapeHtml(t.nom)}</div><div class="form-desc">${escapeHtml(t.description || "")}</div>
        <div class="form-grille gyf-champs" id="form-client-${t.id}">
          ${t.champs.map(ch => `<div class="champ-item groupe" data-champ-slug="${escapeHtml(ch.slug)}"><label>${escapeHtml(ch.nom)}${ch.obligatoire?' <span class="obligatoire">*</span>':''}</label>${champVersInput(ch)}</div>`).join('')}
        </div>
        <button class="principal gyf-btn" style="margin-top:14px;width:100%" onclick="soumettreFormClient(${t.id})">
          ${tj("engine/static/engine/js/index_2.js:74:11")}
        </button>
        <div id="msg-client-${t.id}" style="display:none;margin-top:10px;font-size:13px;color:var(--ok)"></div>
      </div>
    </div>`).join('');
  // Même rendu que sur la page du client : le thème de chaque table (éditeur visuel, formulaire_theme.js).
  if(window.gyFormTheme) tables.forEach(t => { const h = zone.querySelector(`.corps-carte.gyf-hote[data-table-id="${t.id}"]`); if(h) gyFormTheme.appliquer(h, t.theme_formulaire || {}); });
  // Formulaire conditionnel (sections + renvois) : même parcours que sur le portail client.
  tables.forEach(t => {
    _tablesFormClient[t.id] = t;
    const h = zone.querySelector(`.corps-carte.gyf-hote[data-table-id="${t.id}"]`);
    if(h && window.gyFormCondIndex && gyFormCondIndex.actif(t)) gyFormCondIndex.monter(t, h.querySelector(`#form-client-${t.id}`), h.querySelector("button.gyf-btn"));
    if(h && window.gyFormTheme) gyFormTheme.appliquerDetails(h, t.theme_formulaire || {});
  });
  initialiserWidgets(); // branche les toggles (switch) et l'affichage live des barres
}
async function soumettreFormClient(tableId){
  const form = document.getElementById(`form-client-${tableId}`);
  const msg  = document.getElementById(`msg-client-${tableId}`);
  if(!form || !msg){
    // Ne devrait jamais arriver, mais si les id ne correspondent pas on
    // préfère un message clair à un clic qui ne fait "rien".
    notifierErreur(tj("engine/static/engine/js/index_2.js:83:21"));
    return;
  }
  let champs = _champsFormClient[tableId] || [];
  const _tCond = _tablesFormClient[tableId];
  if(_tCond && window.gyFormCondIndex && gyFormCondIndex.actif(_tCond)) champs = gyFormCondIndex.champsActifs(_tCond, form);   // sections non traversées : ni exigées ni envoyées

  try{
    // Validation des champs obligatoires (même règle que le formulaire admin :
    // une case/switch compte toujours comme "rempli").
    let toutesLesCasesValides = true;
    for(const c of champs){
      const el = form.querySelector("#champ_"+c.slug);
      if(!el) continue;
      if(c.type === "formule" || c.type === "constante" || c.type === "horodatage" || c.type === "id_client") continue;
      el.classList.remove("champ-invalide");
      if(!c.obligatoire || TYPES_FICHIER_LOCAL.includes(c.type)) continue;
      let vide;
      if(TYPES_BOOLEEN.includes(c.type)) vide = false;
      else if(c.type === "choix_multiple") vide = el.selectedOptions.length === 0;
      else vide = (el.value || "").trim() === "";
      if(vide){ el.classList.add("champ-invalide"); toutesLesCasesValides = false; }
    }
    if(!toutesLesCasesValides){
      msg.textContent = tj("engine/static/engine/js/index_2.js:105:26");
      msg.style.color = "var(--danger)";
      msg.style.display = "block";
      return;
    }

    const aDesFichiers = champs.some(c => TYPES_FICHIER_LOCAL.includes(c.type) && form.querySelector("#champ_"+c.slug)?.files?.length);

    if(aDesFichiers){
      const fd = new FormData();
      for(const c of champs){
        const el = form.querySelector("#champ_"+c.slug);
        if(!el) continue;
      if(c.type === "formule" || c.type === "constante" || c.type === "horodatage" || c.type === "id_client") continue;
        if(TYPES_BOOLEEN.includes(c.type)) fd.append(c.slug, el.checked ? "1" : "0");
        else if(c.type === "choix_multiple") [...el.selectedOptions].forEach(o => fd.append(c.slug, o.value));
        else if(TYPES_FICHIER_LOCAL.includes(c.type)) { if(el.files[0]) fd.append(c.slug, el.files[0]); }
        else if(c.type === "relation"){
          const rel = _relationValeursPourCreation(c, form);
          for(const cle in rel) fd.append(cle, rel[cle]);
        }
        else fd.append(c.slug, el.value);
      }
      await api(`/api/tables/${tableId}/lignes/`, {method:"POST", body: fd});
    } else {
      const valeurs = {};
      for(const c of champs){
        const el = form.querySelector("#champ_"+c.slug);
        if(!el) continue;
      if(c.type === "formule" || c.type === "constante" || c.type === "horodatage" || c.type === "id_client") continue;
        if(TYPES_BOOLEEN.includes(c.type)) valeurs[c.slug] = el.checked;
        else if(c.type === "choix_multiple") valeurs[c.slug] = [...el.selectedOptions].map(o => o.value);
        else if(c.type === "relation") Object.assign(valeurs, _relationValeursPourCreation(c, form));
        else valeurs[c.slug] = el.value;
      }
      await api(`/api/tables/${tableId}/lignes/`, {method:"POST", body: JSON.stringify(valeurs)});
    }
    msg.textContent = tj("engine/static/engine/js/index_2.js:142:24");
    msg.style.color = "var(--ok)";
    msg.style.display = "block";
    // Réinitialise chaque champ selon son type (checked=false pour les cases,
    // pas seulement value="" qui ne remet pas les cases/switch à zéro).
    for(const c of champs){
      const el = form.querySelector("#champ_"+c.slug);
      if(!el) continue;
      if(c.type === "formule" || c.type === "constante" || c.type === "horodatage" || c.type === "id_client") continue;
      if(TYPES_BOOLEEN.includes(c.type)){ el.checked = false; el.dispatchEvent(new Event("change")); }
      else if(c.type === "barre_progression"){ el.value = 0; el.dispatchEvent(new Event("input")); }
      else if(c.type === "relation"){
        el.value = "";
        const lbl = form.querySelector(`#champ_${c.slug}_label`);
        if(lbl) lbl.textContent = tj("engine/static/engine/js/index_2.js:156:36");
      } else if(TYPES_TENSEUR.includes(c.type)){ GYTenseur.remplir(el, null); }
      else { el.value = ""; }
    }
    setTimeout(() => msg.style.display = "none", 4000);
  }catch(e){
    // Affiche le vrai motif renvoyé par le serveur (ex: type de champ invalide)
    // au lieu d'un message générique, et surligne les champs fautifs si le
    // serveur les a identifiés (e.dansChamps est posé par api() ci-dessus).
    msg.textContent = (e && e.message) ? e.message : tj("engine/static/engine/js/index_2.js:164:55");
    msg.style.color = "var(--danger)";
    msg.style.display = "block";
    if(e && e.champsInvalides){
      for(const slug of Object.keys(e.champsInvalides)){
        const el = form.querySelector("#champ_"+slug);
        if(el) el.classList.add("champ-invalide");
      }
    }
  }
}
// ── Champs "script d'exemple" de la config IA — textarea brut ──
(function(){
  const ta = document.getElementById("cfg-script-exemple");
  if(ta) ta.classList.add("editeur-script-brut");
})();
function _getCfgScript(){ return (document.getElementById("cfg-script-exemple")||{}).value || ""; }
function _setCfgScript(v){ const ta = document.getElementById("cfg-script-exemple"); if(ta) ta.value = v; }
(function(){
  const ta = document.getElementById("cfg-script-exemple-document");
  if(ta) ta.classList.add("editeur-script-brut");
})();
// Meme classe manquante sur les 2 textarea du canal formule : le parent
// .CodeMirror-editeur (min-height:360px) ne s'alignait pas sur la hauteur
// reelle du textbox (rows="10", sans la regle CSS .editeur-script-brut).
(function(){
  ["cfg-script-exemple-formule-colonne", "cfg-script-exemple-formule-table"].forEach(id => {
    const ta = document.getElementById(id);
    if(ta) ta.classList.add("editeur-script-brut");
  });
})();
// Meme classe manquante sur les 2 textarea du canal graphique BI.
(function(){
  ["cfg-script-exemple-graphique-bi-colonne", "cfg-script-exemple-graphique-bi-multiples", "cfg-script-exemple-couche-ontologie", "cfg-script-exemple-modele-ia", "cfg-script-exemple-tache"].forEach(id => {
    const ta = document.getElementById(id);
    if(ta) ta.classList.add("editeur-script-brut");
  });
})();
function _getCfgScriptDocument(){ return (document.getElementById("cfg-script-exemple-document")||{}).value || ""; }
function _setCfgScriptDocument(v){ const ta = document.getElementById("cfg-script-exemple-document"); if(ta) ta.value = v; }
function _getCfgScriptFormuleColonne(){ return (document.getElementById("cfg-script-exemple-formule-colonne")||{}).value || ""; }
function _setCfgScriptFormuleColonne(v){ const ta = document.getElementById("cfg-script-exemple-formule-colonne"); if(ta) ta.value = v; }
function _getCfgScriptFormuleTable(){ return (document.getElementById("cfg-script-exemple-formule-table")||{}).value || ""; }
function _setCfgScriptFormuleTable(v){ const ta = document.getElementById("cfg-script-exemple-formule-table"); if(ta) ta.value = v; }
function _getCfgScriptGraphiqueBiColonne(){ return (document.getElementById("cfg-script-exemple-graphique-bi-colonne")||{}).value || ""; }
function _setCfgScriptGraphiqueBiColonne(v){ const ta = document.getElementById("cfg-script-exemple-graphique-bi-colonne"); if(ta) ta.value = v; }
function _getCfgScriptGraphiqueBiMultiples(){ return (document.getElementById("cfg-script-exemple-graphique-bi-multiples")||{}).value || ""; }
function _setCfgScriptGraphiqueBiMultiples(v){ const ta = document.getElementById("cfg-script-exemple-graphique-bi-multiples"); if(ta) ta.value = v; }
function _getCfgScriptCoucheOntologie(){ return (document.getElementById("cfg-script-exemple-couche-ontologie")||{}).value || ""; }
function _setCfgScriptCoucheOntologie(v){ const ta = document.getElementById("cfg-script-exemple-couche-ontologie"); if(ta) ta.value = v; }
function _getCfgScriptModeleIa(){ return (document.getElementById("cfg-script-exemple-modele-ia")||{}).value || ""; }
function _setCfgScriptModeleIa(v){ const ta = document.getElementById("cfg-script-exemple-modele-ia"); if(ta) ta.value = v; }
function _getCfgScriptTache(){ return (document.getElementById("cfg-script-exemple-tache")||{}).value || ""; }
function _setCfgScriptTache(v){ const ta = document.getElementById("cfg-script-exemple-tache"); if(ta) ta.value = v; }
function activerEditeurScript(carte, demandeId){
  const ta = carte.querySelector(".texte-script-manuel");
  if(!ta || ta._cmDone) return;
  ta._cmDone = true;
  ta.classList.add("editeur-script-brut");
  ta.addEventListener("keydown", ev => {
    if(ev.key === "Tab"){
      ev.preventDefault();
      const start = ta.selectionStart, end = ta.selectionEnd;
      ta.value = ta.value.slice(0, start) + "    " + ta.value.slice(end);
      ta.selectionStart = ta.selectionEnd = start + 4;
    }
  });
}
// Surcharge de btn-voir-script pour activer l'éditeur à l'ouverture
const _origRebrancher = _rebrancher_carte_analyse;
function _patcherBtnScript(carte, a, slug){
  const btnScript = carte.querySelector(".btn-voir-script");
  if(!btnScript) return;
  btnScript.onclick = () => {
    const bloc = carte.querySelector(".bloc-script-manuel");
    const estVisible = bloc.style.display !== "none";
    bloc.style.display = estVisible ? "none" : "block";
    if(!estVisible){
      activerEditeurScript(carte, a.id);
    }
  };
}
// Wrapping de construireCarteAnalyse pour patcher après construction
const _origConstruire = construireCarteAnalyse;
window.construireCarteAnalyse = function(a){
  const carte = _origConstruire(a);
  _patcherBtnScript(carte, a, tableActive);
  return carte;
};
// Idem pour _rebrancher_carte_analyse
const _origRebrancherGlobal = window._rebrancher_carte_analyse;
window._rebrancher_carte_analyse = function(carte, a, slug){
  _origRebrancherGlobal(carte, a, slug);
  _patcherBtnScript(carte, a, slug);
};
