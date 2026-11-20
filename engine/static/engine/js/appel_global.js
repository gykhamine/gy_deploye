// APPELS AUDIO / VIDÉO PERSISTANTS (coque) — le moteur WebRTC vit ICI, dans le document parent qui ne se recharge jamais ;
// les pages (messagerie, tableau de bord, BI…) sont dans un cadre. Changer de page ne coupe donc pas l'appel : tant que
// l'utilisateur reste connecté au serveur (WebSocket de salle ouvert), il peut naviguer et revenir. Signalisation : /ws/appels/<id>/
// (engine/appels.py, consumers.AppelConsumer — inchangés). Les pages pilotent l'appel via `window.top.Appel`.
(function(){
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => "&#" + c.charCodeAt(0) + ";");
  const CSRF = () => document.cookie.match(/gy_csrftoken=([^;]+)/)?.[1] || "";
  const wsUrl = chemin => (location.protocol === "https:" ? "wss://" : "ws://") + location.host + chemin;
  async function api(url, corps){
    const res = await fetch(url, corps === undefined ? {} : {method: "POST", headers: {"X-CSRFToken": CSRF(), "Content-Type": "application/json"}, body: JSON.stringify(corps)});
    const d = await res.json().catch(() => ({})); if (!res.ok) throw d; return d;
  }
  function avis(m){ const t = $("coque-avis"); t.textContent = m; t.hidden = false; clearTimeout(t._t); t._t = setTimeout(() => { t.hidden = true; }, 5000); }

  const A = {ws: null, conv: null, titre: "", ice: [], max: 10, local: null, ecran: null, video: false, pairs: {}, moi: null, entrant: null, sonnerie: null};

  function majBarre(){
    const en = !!A.ws;
    $("appel").hidden = !en;
    $("appel-cam").hidden = !A.video; $("appel-ecran").hidden = !A.video;
    $("appel-titre-texte").textContent = A.titre + (A.video ? tj("engine/static/engine/js/appel_global.js:22:64") : " — audio");
    document.body.classList.toggle("en-appel", en);
    document.dispatchEvent(new CustomEvent("appel-etat", {detail: etat()}));
  }
  const etat = () => ({enCours: !!A.ws, conversation: A.conv, video: A.video, reduit: $("appel").classList.contains("mini")});
  function reduire(oui){
    const el = $("appel"); el.classList.toggle("mini", oui === undefined ? !el.classList.contains("mini") : !!oui);
    $("appel-reduire").textContent = el.classList.contains("mini") ? "⤢" : "⤡";
    $("appel-reduire").title = el.classList.contains("mini") ? tj("engine/static/engine/js/appel_global.js:30:65") : tj("engine/static/engine/js/appel_global.js:30:86");
  }

  async function entrer(conv, video, sonner, titre){
    if (A.ws || A.conv) { avis(tj("engine/static/engine/js/appel_global.js:34:33")); reduire(false); return; }
    A.conv = conv;   // verrou immédiat : deux clics rapprochés ne lancent pas deux appels
    try {
      const cfg = await api("/api/messagerie/conversations/" + conv + "/appel/config/");
      A.ice = cfg.ice; A.max = cfg.max; A.video = !!video; A.pairs = {};
      A.local = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}, video: video ? {width: {ideal: 1280}, height: {ideal: 720}} : false});
    } catch(e){ A.conv = null; avis(tj("engine/static/engine/js/appel_global.js:40:38")); return; }
    A.titre = titre || tj("engine/static/engine/js/appel_global.js:lbl_appel");
    $("appel-micro").classList.remove("coupe"); $("appel-cam").classList.remove("coupe"); $("appel-ecran").classList.remove("coupe");
    reduire(false);
    ajouterTuile("moi", tj("engine/static/engine/js/appel_global.js:lbl_vous"), A.local, true);
    A.ws = new WebSocket(wsUrl("/ws/appels/" + conv + "/"));
    A.ws.onmessage = ev => { try { recevoir(JSON.parse(ev.data)); } catch(e){ console.error(e); } };
    let ouvert = false; A.ws.onopen = () => { ouvert = true; };
    A.ws.onclose = ev => {
      if (!A.ws) return;
      if (!ouvert) avis(tj("engine/static/engine/js/appel_global.js:50:26"));
      else if (ev.code === 4009) avis(tj("engine/static/engine/js/appel_global.js:lbl_appel_complet") + A.max + tj("engine/static/engine/js/appel_global.js:51:68"));
      else avis(tj("engine/static/engine/js/appel_global.js:52:18"));
      fin();
    };
    majBarre();
    if (sonner) { try { await api("/api/messagerie/conversations/" + conv + "/appel/sonner/", {video: !!video}); } catch(e){ /* l'appel reste ouvert */ } }
  }
  function envoyerSignal(pair, data){ if (A.ws && A.ws.readyState === 1) A.ws.send(JSON.stringify({type: "signal", a: pair, data})); }
  function creerPair(pair, nom, offrir){
    if (A.pairs[pair]) return A.pairs[pair];
    const pc = new RTCPeerConnection({iceServers: A.ice}), p = {pc, nom, file: [], flux: new MediaStream()};
    A.pairs[pair] = p;
    A.local.getTracks().forEach(t => pc.addTrack(t, A.local));
    pc.onicecandidate = e => { if (e.candidate) envoyerSignal(pair, {type: "candidate", candidate: e.candidate}); };
    pc.ontrack = e => { p.flux.addTrack(e.track); ajouterTuile(pair, nom, p.flux, false); };
    pc.onconnectionstatechange = () => { if (pc.connectionState === "failed") { fermerPair(pair); avis(tj("engine/static/engine/js/appel_global.js:66:105") + nom + tj("engine/static/engine/js/appel_global.js:66:150")); } };
    if (offrir) pc.createOffer().then(o => pc.setLocalDescription(o)).then(() => envoyerSignal(pair, {type: "offer", sdp: pc.localDescription.sdp})).catch(console.error);
    return p;
  }
  async function recevoir(m){
    if (!A.ws) return;
    if (m.type === "bienvenue") A.moi = m.moi;
    else if (m.type === "complet") { avis(tj("engine/static/engine/js/appel_global.js:lbl_appel_complet") + A.max + tj("engine/static/engine/js/appel_global.js:73:72")); fin(); }
    else if (m.type === "present") creerPair(m.pair, m.nom, true);
    else if (m.type === "depart") fermerPair(m.pair);
    else if (m.type === "signal") {
      const d = m.data, p = creerPair(m.de, m.nom, false), pc = p.pc;
      if (d.type === "offer") {
        await pc.setRemoteDescription({type: "offer", sdp: d.sdp});
        for (const c of p.file.splice(0)) await pc.addIceCandidate(c).catch(console.error);
        await pc.setLocalDescription(await pc.createAnswer());
        envoyerSignal(m.de, {type: "answer", sdp: pc.localDescription.sdp});
      } else if (d.type === "answer") {
        await pc.setRemoteDescription({type: "answer", sdp: d.sdp});
        for (const c of p.file.splice(0)) await pc.addIceCandidate(c).catch(console.error);
      } else if (d.type === "candidate") {
        if (pc.remoteDescription) await pc.addIceCandidate(d.candidate).catch(console.error); else p.file.push(d.candidate);
      }
    }
  }
  function ajouterTuile(id, nom, flux, local){
    let t = $("appel-t-" + id);
    if (!t) {
      t = document.createElement("div"); t.id = "appel-t-" + id; t.className = "appel-tuile";
      t.innerHTML = `<video autoplay playsinline ${local ? "muted" : ""}></video><span>${esc(nom)}</span>`;
      $("appel-grille").appendChild(t);
    }
    const v = t.querySelector("video"); if (v.srcObject !== flux) v.srcObject = flux;
    $("appel-grille").dataset.n = $("appel-grille").children.length;
  }
  function fermerPair(pair){
    const p = A.pairs[pair]; if (p) { p.pc.close(); delete A.pairs[pair]; }
    const t = $("appel-t-" + pair); if (t) t.remove();
    $("appel-grille").dataset.n = $("appel-grille").children.length;
  }
  function basculerMicro(){ const t = A.local && A.local.getAudioTracks()[0]; if (t) { t.enabled = !t.enabled; $("appel-micro").classList.toggle("coupe", !t.enabled); } }
  function basculerCamera(){ const t = A.local && A.local.getVideoTracks()[0]; if (t) { t.enabled = !t.enabled; $("appel-cam").classList.toggle("coupe", !t.enabled); } }
  async function remplacerVideo(piste){
    for (const p of Object.values(A.pairs)) { const s = p.pc.getSenders().find(x => x.track && x.track.kind === "video"); if (s) await s.replaceTrack(piste); }
    const v = document.querySelector("#appel-t-moi video"); if (v) v.srcObject = new MediaStream([piste, ...A.local.getAudioTracks()]);
  }
  async function partagerEcran(){
    if (!A.local || !navigator.mediaDevices.getDisplayMedia) return;
    if (A.ecran) { A.ecran.getTracks().forEach(t => t.stop()); return; }   // second clic : arrêter le partage
    try { A.ecran = await navigator.mediaDevices.getDisplayMedia({video: true}); } catch(e){ return; }
    const piste = A.ecran.getVideoTracks()[0];
    piste.onended = async () => { A.ecran = null; $("appel-ecran").classList.remove("coupe"); const c = A.local && A.local.getVideoTracks()[0]; if (c) await remplacerVideo(c); };
    $("appel-ecran").classList.add("coupe"); await remplacerVideo(piste);
  }
  function fin(){
    const ws = A.ws; A.ws = null; if (ws) { try { ws.close(); } catch(e){} }
    Object.keys(A.pairs).forEach(fermerPair);
    [A.local, A.ecran].forEach(f => f && f.getTracks().forEach(t => t.stop()));
    A.local = A.ecran = null; A.conv = null; $("appel-grille").innerHTML = ""; $("appel-grille").dataset.n = 0;
    reduire(false); majBarre();
  }

  // Sonnerie : l'appelant prévient par le WebSocket de notifications. La coque l'écoute en permanence : on sonne quelle que soit la page ouverte.
  let _echecsWs = 0;   // reconnexion avec attente croissante (5 s → 60 s max) : plus de 404 toutes les 5 s si le serveur n'est pas ASGI
  function ecouter(){
    const ws = new WebSocket(wsUrl("/ws/notifications/"));
    ws.onopen = () => { _echecsWs = 0; };
    ws.onmessage = ev => { try { const d = JSON.parse(ev.data); if (d.type === "appel_entrant" && !A.ws) sonner(d); else if (window.GYNotif) GYNotif.recevoir(d); } catch(e){} };
    ws.onclose = () => { _echecsWs++; setTimeout(ecouter, Math.min(60000, 5000 * Math.pow(2, Math.min(_echecsWs - 1, 4)))); };
  }
  function sonner(d){
    A.entrant = d; $("appel-entrant-nom").textContent = d.de;
    $("appel-entrant-type").textContent = (d.video ? tj("engine/static/engine/js/appel_global.js:136:55") : tj("engine/static/engine/js/appel_global.js:136:82")) + (d.titre && d.titre !== d.de ? " — " + d.titre : "");
    $("appel-entrant").hidden = false; clearTimeout(A.sonnerie); A.sonnerie = setTimeout(refuser, 40000);
    const corps = $("appel-entrant-type").textContent;
    if (window.GYNotif) GYNotif.afficher({titre: d.de, message: corps, tag: "gy-appel", seulement_masquee: true, persistante: true});
    else if (window.Notification && Notification.permission === "granted" && document.hidden) new Notification(d.de, {body: corps});
  }
  function refuser(){ A.entrant = null; clearTimeout(A.sonnerie); $("appel-entrant").hidden = true; }
  async function accepter(){
    const d = A.entrant; refuser(); if (!d) return;
    await entrer(d.conversation_id, d.video, false, d.titre || d.de);
  }

  window.Appel = {
    possible: !!navigator.mediaDevices && !!window.RTCPeerConnection,
    entrer, etat, reduire, raccrocher: fin, basculerMicro, basculerCamera, partagerEcran, accepter, refuser,
  };
  $("appel-micro").onclick = basculerMicro; $("appel-cam").onclick = basculerCamera; $("appel-ecran").onclick = partagerEcran;
  $("appel-fin").onclick = fin; $("appel-reduire").onclick = () => reduire();
  $("appel-entrant-oui").onclick = accepter; $("appel-entrant-non").onclick = refuser;
  window.addEventListener("beforeunload", () => { if (A.ws) fin(); });   // fermeture réelle de l'application seulement : la coque ne se recharge pas à la navigation
  ecouter();   // la permission des notifications est demandée par GYNotif au premier clic (un geste de l'utilisateur est exigé)
})();
