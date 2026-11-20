// ── Sélecteur de lieu partagé (champ « géolocalisation ») ─────────────────────────────────────────────────────────
// UNE seule carte pour l'agent (index_1.js) ET le client (client_1.js) : « 🗺️ Choisir sur la carte » (clic, recherche d'adresse) et
// « 📍 Ma position ». Le champ reçoit « lat, lon » ; l'input est « champ_<slug> » côté agent, « champ-<slug> » côté client.
function _erreurLieu(m){ if(typeof notifierErreur === "function") notifierErreur(m); else GY.alerter(m); }

// Bouton "📍 Ma position" d'un champ géolocalisation : API de localisation du navigateur (HTTPS ou localhost requis).
function _positionGPS(slug){
  const cible = document.getElementById("champ_"+slug) || document.getElementById("champ-"+slug);
  if(!navigator.geolocation) return _erreurLieu(tj("engine/static/engine/js/index_1.js:5289:53"));
  navigator.geolocation.getCurrentPosition(
    p => { cible.value = p.coords.latitude.toFixed(6)+", "+p.coords.longitude.toFixed(6); },
    e => _erreurLieu(tj("engine/static/engine/js/selecteur_lieu.js:lbl_position_indispo", {v1: e.message})), {enableHighAccuracy:true, timeout:15000});
}

// Bouton "🗺️ Carte" d'un champ géolocalisation : l'utilisateur clique sur une carte (tuiles OpenStreetMap servies par le serveur) ou cherche
// une adresse ; le champ reçoit « lat, lon ». Le backend (_lieu_vers_coordonnees) sait déjà lire cette valeur ou un nom de lieu.
function _choisirSurCarte(slug, champId){
  const input = document.getElementById("champ_"+slug) || document.getElementById("champ-"+slug);   // « champ_ » côté agent, « champ- » côté client
  if(!input) return;
  const tid = (typeof tableActive !== "undefined" && tableActive && tableActive.id) ? tableActive.id : (typeof tables !== "undefined" && Array.isArray(tables) ? (tables.find(t => (t.champs||[]).some(x => Number(x.id) === Number(champId))) || {}).id : null);
  const dejaPos = (String(input.value).match(/-?\d+(?:\.\d+)?/g) || []).map(Number);
  const S = {lat: 0, lon: 20, z: 2, pt: null};
  if(dejaPos.length >= 2 && Math.abs(dejaPos[0]) <= 90 && Math.abs(dejaPos[1]) <= 180){ S.lat = dejaPos[0]; S.lon = dejaPos[1]; S.z = 15; S.pt = [S.lat, S.lon]; }
  const fond = document.createElement("div");
  fond.style.cssText = "position:fixed;inset:0;background:color-mix(in srgb,var(--voile,#000) 60%,transparent);z-index:100000;display:flex;align-items:center;justify-content:center;padding:12px";
  fond.innerHTML = `<div style="background:var(--card,#1b2630);color:var(--text,#e6edf3);border:1px solid var(--border,#34495e);border-radius:10px;width:min(760px,100%);max-height:100%;display:flex;flex-direction:column;overflow:hidden">
    <div style="display:flex;gap:6px;padding:10px;align-items:center;flex-wrap:wrap">
      <input type="text" class="gc-q" placeholder="${tj("engine/static/engine/js/selecteur_lieu.js:28:52")}" style="flex:1;min-width:160px">
      <button type="button" class="gc-ok-q" style="padding:5px 10px">🔍</button>
      <button type="button" class="gc-moi" style="padding:5px 10px">${tj("engine/static/engine/js/selecteur_lieu.js:30:69")}</button>
    </div>
    <div class="gc-res" style="display:none;padding:0 10px 6px;font-size:12px"></div>
    <div class="gc-carte" style="position:relative;height:min(55vh,420px);background:var(--fond-sombre,#0d1419);overflow:hidden;touch-action:none;cursor:crosshair;user-select:none">
      <div class="gc-tuiles" style="position:absolute;inset:0"></div>
      <div class="gc-marq" style="position:absolute;display:none;transform:translate(-50%,-100%);font-size:28px;line-height:1;pointer-events:none">📍</div>
      <div style="position:absolute;right:8px;top:8px;display:flex;flex-direction:column;gap:4px">
        <button type="button" class="gc-plus" style="width:32px;height:32px;font-size:18px">+</button>
        <button type="button" class="gc-moins" style="width:32px;height:32px;font-size:18px">−</button>
      </div>
      <div style="position:absolute;left:6px;bottom:4px;font-size:10px;color:var(--texte-discret,#9aa7b2);background:color-mix(in srgb,var(--voile,#000) 50%,transparent);padding:1px 4px;border-radius:3px">${tj("engine/static/engine/js/selecteur_lieu.js:40:206")}</div>
    </div>
    <div style="display:flex;gap:8px;padding:10px;align-items:center;justify-content:space-between;flex-wrap:wrap">
      <span class="gc-info" style="font-size:12px;color:var(--muted,#9aa7b2)">${tj("engine/static/engine/js/selecteur_lieu.js:43:79")}</span>
      <span><button type="button" class="gc-annuler" style="padding:5px 12px">${tj("engine/static/engine/js/selecteur_lieu.js:44:79")}</button> <button type="button" class="gc-valider" disabled style="padding:5px 12px">${tj("engine/static/engine/js/selecteur_lieu.js:44:171")}</button></span>
    </div></div>`;
  document.body.appendChild(fond);
  const q = s => fond.querySelector(s), zone = q(".gc-carte"), couche = q(".gc-tuiles"), marq = q(".gc-marq");
  const mx = z => 256 * Math.pow(2, z);
  const px = (lat, lon, z) => { const sn = Math.sin(lat*Math.PI/180); return [(lon+180)/360*mx(z), (0.5 - Math.log((1+sn)/(1-sn))/(4*Math.PI))*mx(z)]; };
  const ll = (x, y, z) => { const n = Math.PI - 2*Math.PI*y/mx(z); return [180/Math.PI*Math.atan(0.5*(Math.exp(n)-Math.exp(-n))), x/mx(z)*360-180]; };
  const cache = new Map();
  function dessiner(){
    const W = zone.clientWidth, H = zone.clientHeight, [cx, cy] = px(S.lat, S.lon, S.z), n = Math.pow(2, S.z);
    const x0 = Math.floor((cx-W/2)/256), x1 = Math.floor((cx+W/2)/256), y0 = Math.floor((cy-H/2)/256), y1 = Math.floor((cy+H/2)/256);
    const vus = new Set();
    for(let x = x0; x <= x1; x++) for(let y = y0; y <= y1; y++){
      if(y < 0 || y >= n) continue;
      const cle = S.z+"/"+(((x%n)+n)%n)+"/"+y+"@"+x; vus.add(cle);
      let im = cache.get(cle);
      if(!im){ im = document.createElement("img"); im.draggable = false; im.style.cssText = "position:absolute;width:256px;height:256px;pointer-events:none"; im.src = "/api/geolocalisation/tuiles/"+S.z+"/"+(((x%n)+n)%n)+"/"+y+".png"; im.onerror = () => { im.style.visibility = "hidden"; }; couche.appendChild(im); cache.set(cle, im); }
      im.style.left = Math.round(x*256-cx+W/2)+"px"; im.style.top = Math.round(y*256-cy+H/2)+"px";
    }
    for(const [cle, im] of cache) if(!vus.has(cle)){ im.remove(); cache.delete(cle); }
    if(S.pt){ const [a, b] = px(S.pt[0], S.pt[1], S.z); marq.style.display = "block"; marq.style.left = (a-cx+W/2)+"px"; marq.style.top = (b-cy+H/2)+"px"; } else marq.style.display = "none";
  }
  function poser(lat, lon){
    S.pt = [lat, lon]; q(".gc-valider").disabled = false;
    q(".gc-info").textContent = tj("engine/static/engine/js/selecteur_lieu.js:lbl_lieu_choisi", {v1: lat.toFixed(6), v2: lon.toFixed(6)});
    dessiner();
  }
  function centrer(lat, lon, z){ S.lat = lat; S.lon = lon; if(z) S.z = z; dessiner(); }
  if(S.pt){ poser(S.pt[0], S.pt[1]); }
  setTimeout(dessiner, 0);
  // Glisser pour déplacer, simple clic pour choisir le lieu.
  let drag = null;
  zone.addEventListener("pointerdown", e => { drag = {x: e.clientX, y: e.clientY, lat: S.lat, lon: S.lon, bouge: false}; zone.setPointerCapture(e.pointerId); });
  zone.addEventListener("pointermove", e => {
    if(!drag) return;
    const dx = e.clientX-drag.x, dy = e.clientY-drag.y;
    if(Math.abs(dx)+Math.abs(dy) > 4) drag.bouge = true;
    if(drag.bouge){ const [cx, cy] = px(drag.lat, drag.lon, S.z); const r = ll(cx-dx, cy-dy, S.z); S.lat = Math.max(-85, Math.min(85, r[0])); S.lon = r[1]; dessiner(); }
  });
  zone.addEventListener("pointerup", e => {
    if(drag && !drag.bouge){
      const r = zone.getBoundingClientRect(), [cx, cy] = px(S.lat, S.lon, S.z);
      const p = ll(cx+(e.clientX-r.left)-r.width/2, cy+(e.clientY-r.top)-r.height/2, S.z);
      poser(p[0], ((p[1]+540)%360)-180);
    }
    drag = null;
  });
  zone.addEventListener("wheel", e => { e.preventDefault(); S.z = Math.max(1, Math.min(19, S.z + (e.deltaY < 0 ? 1 : -1))); dessiner(); }, {passive: false});
  q(".gc-plus").onclick = () => { S.z = Math.min(19, S.z+1); dessiner(); };
  q(".gc-moins").onclick = () => { S.z = Math.max(1, S.z-1); dessiner(); };
  q(".gc-moi").onclick = () => {
    if(!navigator.geolocation) return _erreurLieu(tj("engine/static/engine/js/selecteur_lieu.js:95:52"));
    navigator.geolocation.getCurrentPosition(p => { centrer(p.coords.latitude, p.coords.longitude, 16); poser(p.coords.latitude, p.coords.longitude); }, e => _erreurLieu(tj("engine/static/engine/js/selecteur_lieu.js:lbl_position_indispo", {v1: e.message})), {enableHighAccuracy: true, timeout: 15000});
  };
  async function chercher(){
    const txt = q(".gc-q").value.trim(), res = q(".gc-res");
    if(!txt) return;
    if(!tid){ res.style.display = "block"; res.textContent = tj("engine/static/engine/js/selecteur_lieu.js:101:63"); return; }
    res.style.display = "block"; res.textContent = tj("engine/static/engine/js/selecteur_lieu.js:102:53");
    try {
      const r = await fetch("/api/tables/"+tid+"/geolocalisation/adresse/?q="+encodeURIComponent(txt), {credentials: "same-origin"});
      const d = await r.json();
      if(!r.ok) throw new Error(d.erreur || tj("engine/static/engine/js/selecteur_lieu.js:106:46"));
      const L = d.resultats || [];
      if(!L.length){ res.textContent = tj("engine/static/engine/js/selecteur_lieu.js:108:41"); return; }
      res.innerHTML = "";
      L.slice(0, 6).forEach(x => {
        const lat = Number(x.lat != null ? x.lat : x.latitude), lon = Number(x.lon != null ? x.lon : (x.lng != null ? x.lng : x.longitude));
        const a = document.createElement("a"); a.href = "#"; a.style.cssText = "display:block;color:var(--accent);padding:2px 0";
        a.textContent = [x.nom, x.detail].filter(Boolean).join(" — ") || (lat+", "+lon);
        a.onclick = ev => { ev.preventDefault(); if(isFinite(lat) && isFinite(lon)){ centrer(lat, lon, 16); poser(lat, lon); res.style.display = "none"; } };
        res.appendChild(a);
      });
    } catch(err){ res.textContent = String(err.message || err); }
  }
  q(".gc-ok-q").onclick = chercher;
  q(".gc-q").addEventListener("keydown", e => { if(e.key === "Enter"){ e.preventDefault(); chercher(); } });
  const fermer = () => fond.remove();
  q(".gc-annuler").onclick = fermer;
  fond.addEventListener("mousedown", e => { if(e.target === fond) fermer(); });
  q(".gc-valider").onclick = () => {
    if(!S.pt) return;
    input.value = S.pt[0].toFixed(6)+", "+S.pt[1].toFixed(6);
    input.dispatchEvent(new Event("input", {bubbles: true})); input.dispatchEvent(new Event("change", {bubbles: true}));
    fermer();
  };
}

