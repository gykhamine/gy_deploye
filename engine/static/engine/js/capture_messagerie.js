/* CAPTURE MESSAGERIE — injecté dans la page Messagerie par engine.middleware.CaptureMediaMiddleware (aucun gabarit modifié).
   1) 🎙 Message vocal : un clic démarre, et l'on VOIT l'enregistrement (barre rouge clignotante, durée, niveau sonore) avec
      « Envoyer » (arrête et envoie) et « Annuler » (jette l'enregistrement).
   2) 📷 Caméra : ouvre directement la caméra (aperçu en direct, Photo / Vidéo, caméra avant/arrière, aperçu avant d'utiliser) au lieu
      du sélecteur de fichiers. Le résultat est joint au message comme n'importe quelle pièce jointe.
   Les deux cas sont gérés : si le navigateur n'a pas d'API caméra/micro (page non sécurisée, ancien navigateur), si l'accès est refusé ou si
   aucun appareil n'existe, on retombe sur le comportement d'origine (champ fichier / ancien enregistreur) — rien n'est perdu.
   Les textes viennent de l'attribut data-textes de la balise (déjà traduits par t()). Désactivable : GYPLATFORM_CAPTURE_MEDIA=0. */
(function () {
  "use strict";
  if (window.__gyCaptureMsg) return;
  window.__gyCaptureMsg = true;
  const ME = document.currentScript;
  let TXT = {};
  try { TXT = JSON.parse((ME && ME.dataset.textes) || "{}"); } catch (_) { TXT = {}; }
  const tx = (k, repli) => TXT[k] || repli;
  const $ = id => document.getElementById(id);
  const apiMedia = () => !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
  const dire = (m, erreur) => { try { if (typeof toast === "function") return toast(m, !!erreur); } catch (_) { /* repli ci-dessous */ } try { if (window.GY && GY.alerter) GY.alerter(m); } catch (_) { /* rien */ } };
  const mm = s => String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(Math.floor(s % 60)).padStart(2, "0");
  const typeSupporte = liste => { for (const t of liste) { try { if (MediaRecorder.isTypeSupported(t)) return t; } catch (_) { /* suivant */ } } return ""; };
  const extension = type => /ogg/.test(type) ? "ogg" : /mp4|m4a|aac/.test(type) ? (/^video/.test(type) ? "mp4" : "m4a") : /^video/.test(type) ? "webm" : "weba";

  // ── Pièce jointe : même chemin que le champ fichier d'origine (joindre), sinon repli direct sur S.piece.
  function attacher(fichier) {
    const champ = $("msg-camera");
    try {
      const dt = new DataTransfer(); dt.items.add(fichier); champ.files = dt.files;
      if (typeof joindre === "function") { joindre(champ); return; }
    } catch (_) { /* repli ci-dessous */ }
    try {
      S.piece = fichier;
      const z = $("msg-piece"); z.hidden = false; z.textContent = fichier.name + " ";
      const a = document.createElement("a"); a.href = "#"; a.className = "btn petit"; a.textContent = "✕";
      a.onclick = ev => { ev.preventDefault(); if (typeof retirer === "function") retirer(); else { S.piece = null; z.hidden = true; } };
      z.appendChild(a);
    } catch (_) { dire(tx("erreur", tj("engine/static/engine/js/capture_messagerie.js:37:38")), true); }
  }
  const champFichierOrigine = () => { const c = $("msg-camera"); if (c) c.click(); };

  // ── Styles
  const st = document.createElement("style");
  st.textContent = "@keyframes gy-cm-pouls{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.35;transform:scale(.8)}}"
    + "#msg-micro.gy-cm-actif{background:var(--danger,#dc2626)!important;color:#fff!important;border-color:var(--danger,#dc2626)!important;animation:gy-cm-pouls 1s ease-in-out infinite}"
    + "#gy-cm-barre{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:8px 12px;margin:6px 0;border-radius:12px;border:1px solid var(--danger,#dc2626);"
    + "background:color-mix(in srgb,var(--danger,#dc2626) 14%,transparent);color:inherit;font:14px system-ui,sans-serif}"
    + "#gy-cm-barre[hidden]{display:none}"
    + "#gy-cm-barre .gy-cm-point{width:12px;height:12px;border-radius:50%;background:var(--danger,#dc2626);animation:gy-cm-pouls 1s ease-in-out infinite}"
    + "#gy-cm-barre .gy-cm-temps{font-variant-numeric:tabular-nums;font-weight:600;min-width:46px}"
    + "#gy-cm-barre .gy-cm-niveau{flex:1;min-width:80px;height:8px;border-radius:4px;background:rgba(128,128,128,.3);overflow:hidden}"
    + "#gy-cm-barre .gy-cm-niveau i{display:block;height:100%;width:0;background:var(--danger,#dc2626);transition:width .08s linear}"
    + "#gy-cm-fond{position:fixed;inset:0;z-index:2147482000;display:none;align-items:center;justify-content:center;padding:14px;background:rgba(8,12,20,.82)}"
    + "#gy-cm-fond.on{display:flex}"
    + "#gy-cm-boite{width:min(560px,100%);max-height:100%;display:flex;flex-direction:column;gap:10px;padding:14px;border-radius:16px;overflow:auto;"
    + "background:var(--fond-info,#0f172a);color:var(--texte-clair,#e8e8e8);border:1px solid var(--accent,#38bdf8);font:14px system-ui,sans-serif}"
    + "#gy-cm-boite h3{margin:0;font-size:15px}"
    + "#gy-cm-scene{position:relative;background:#000;border-radius:10px;overflow:hidden;min-height:180px;display:flex;align-items:center;justify-content:center}"
    + "#gy-cm-scene video,#gy-cm-scene img{width:100%;max-height:52vh;object-fit:contain;display:block}"
    + "#gy-cm-scene video.miroir{transform:scaleX(-1)}"
    + "#gy-cm-rec{position:absolute;top:8px;left:8px;display:none;align-items:center;gap:6px;padding:4px 9px;border-radius:12px;background:rgba(0,0,0,.6);color:#fff;font-size:12px}"
    + "#gy-cm-rec .gy-cm-point{width:10px;height:10px;border-radius:50%;background:var(--danger,#dc2626);animation:gy-cm-pouls 1s ease-in-out infinite}"
    + "#gy-cm-err{display:none;padding:8px 10px;border-radius:8px;background:color-mix(in srgb,var(--danger,#dc2626) 20%,transparent)}"
    + "#gy-cm-modes,#gy-cm-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center;justify-content:center}"
    + "#gy-cm-boite button{min-height:40px;padding:6px 16px;border-radius:999px;border:1px solid var(--accent,#38bdf8);background:transparent;color:inherit;cursor:pointer;font:inherit}"
    + "#gy-cm-boite button:hover,#gy-cm-boite button:focus-visible{background:var(--accent,#38bdf8);color:#000;outline:none}"
    + "#gy-cm-boite button.sel{background:var(--accent,#38bdf8);color:#000}"
    + "#gy-cm-boite button.principal{background:var(--danger,#dc2626);border-color:var(--danger,#dc2626);color:#fff}"
    + "#gy-cm-boite button[hidden]{display:none}"
    + "@media (prefers-reduced-motion:reduce){#msg-micro.gy-cm-actif,#gy-cm-barre .gy-cm-point,#gy-cm-rec .gy-cm-point{animation:none}}";
  document.head.appendChild(st);

  // ════════════ 1) Message vocal ════════════
  const V = { rec: null, flux: null, morceaux: [], debut: 0, minuteur: null, ctx: null, annule: false, barre: null };

  function barreVocale() {
    if (V.barre) return V.barre;
    const b = document.createElement("div"); b.id = "gy-cm-barre"; b.hidden = true; b.setAttribute("role", "status"); b.setAttribute("aria-live", "polite");
    b.innerHTML = '<span class="gy-cm-point" aria-hidden="true"></span><b></b><span class="gy-cm-temps">00:00</span><span class="gy-cm-niveau" aria-hidden="true"><i></i></span>'
      + '<button type="button" class="petit principal" data-a="envoyer"></button><button type="button" class="petit" data-a="annuler"></button>';
    b.children[1].textContent = tx("enregistrement", tj("engine/static/engine/js/capture_messagerie.js:80:55"));
    b.querySelector("[data-a=envoyer]").textContent = "■ " + tx("envoyer", tj("engine/static/engine/js/capture_messagerie.js:81:77"));
    b.querySelector("[data-a=annuler]").textContent = "✕ " + tx("annuler", tj("engine/static/engine/js/capture_messagerie.js:82:77"));
    b.querySelector("[data-a=envoyer]").onclick = () => arreterVocal(false);
    b.querySelector("[data-a=annuler]").onclick = () => arreterVocal(true);
    const form = $("msg-saisie"); form.parentNode.insertBefore(b, form);
    V.barre = b; return b;
  }
  function niveauSonore(flux) {   // facultatif : sans AudioContext, seule la durée est affichée
    try {
      const AC = window.AudioContext || window.webkitAudioContext; if (!AC) return;
      V.ctx = new AC(); const an = V.ctx.createAnalyser(); an.fftSize = 256;
      V.ctx.createMediaStreamSource(flux).connect(an);
      const buf = new Uint8Array(an.frequencyBinCount), jauge = V.barre.querySelector(".gy-cm-niveau i");
      V.mesure = () => { if (!V.rec) return; an.getByteTimeDomainData(buf); let m = 0; for (const v of buf) m = Math.max(m, Math.abs(v - 128)); jauge.style.width = Math.min(100, m * 1.6) + "%"; };
    } catch (_) { V.ctx = null; V.mesure = null; }
  }
  async function demarrerVocal() {
    const micro = $("msg-micro");
    try { V.flux = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch (e) { dire(tx("micro_refuse", tj("engine/static/engine/js/capture_messagerie.js:100:42")), true); return; }
    const type = typeSupporte(["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"]);
    try { V.rec = type ? new MediaRecorder(V.flux, { mimeType: type }) : new MediaRecorder(V.flux); }
    catch (e) { V.flux.getTracks().forEach(t => t.stop()); V.flux = null; dire(tx("micro_refuse", tj("engine/static/engine/js/capture_messagerie.js:103:100")), true); return; }
    V.morceaux = []; V.annule = false;
    V.rec.ondataavailable = ev => { if (ev.data && ev.data.size) V.morceaux.push(ev.data); };
    V.rec.onstop = finVocal;
    barreVocale().hidden = false; micro.classList.add("gy-cm-actif"); micro.setAttribute("aria-pressed", "true");
    micro.title = tx("enregistrement", tj("engine/static/engine/js/capture_messagerie.js:108:41"));
    niveauSonore(V.flux);
    V.rec.start(); V.debut = Date.now();
    V.minuteur = setInterval(() => {
      V.barre.querySelector(".gy-cm-temps").textContent = mm((Date.now() - V.debut) / 1000);
      if (V.mesure) V.mesure();
    }, 120);
  }
  function arreterVocal(annule) {
    V.annule = !!annule;
    if (V.rec && V.rec.state !== "inactive") V.rec.stop(); else finVocal();
  }
  async function finVocal() {
    clearInterval(V.minuteur); V.minuteur = null;
    if (V.flux) V.flux.getTracks().forEach(t => t.stop());
    if (V.ctx) { try { V.ctx.close(); } catch (_) { /* déjà fermé */ } }
    const micro = $("msg-micro");
    micro.classList.remove("gy-cm-actif"); micro.removeAttribute("aria-pressed");
    if (V.barre) { V.barre.hidden = true; V.barre.querySelector(".gy-cm-niveau i").style.width = "0"; V.barre.querySelector(".gy-cm-temps").textContent = "00:00"; }
    const rec = V.rec, morceaux = V.morceaux, annule = V.annule;
    V.rec = null; V.flux = null; V.ctx = null; V.mesure = null; V.morceaux = [];
    if (annule || !morceaux.length) return;
    const type = (rec && rec.mimeType) || "audio/webm";
    attacher(new File(morceaux, "vocal." + extension(type), { type }));
    try { await envoyer(new Event("submit")); } catch (_) { /* envoyer() affiche déjà l'erreur */ }
  }

  // ════════════ 2) Caméra directe ════════════
  const C = { flux: null, mode: "photo", face: "environment", rec: null, morceaux: [], blob: null, url: null, debut: 0, minuteur: null, fond: null, nbCameras: 2 };

  function construireCamera() {
    if (C.fond) return;
    const f = document.createElement("div"); f.id = "gy-cm-fond"; f.setAttribute("role", "dialog"); f.setAttribute("aria-modal", "true");
    f.innerHTML = '<div id="gy-cm-boite"><h3></h3>'
      + '<div id="gy-cm-modes"><button type="button" data-m="photo"></button><button type="button" data-m="video"></button></div>'
      + '<div id="gy-cm-scene"><video playsinline muted autoplay></video><img alt="" hidden><video id="gy-cm-lecture" playsinline controls hidden></video>'
      + '<div id="gy-cm-rec"><span class="gy-cm-point"></span><span id="gy-cm-temps">00:00</span></div></div>'
      + '<div id="gy-cm-err"></div>'
      + '<div id="gy-cm-actions"><button type="button" data-a="declencher" class="principal"></button><button type="button" data-a="basculer"></button>'
      + '<button type="button" data-a="utiliser" hidden></button><button type="button" data-a="reprendre" hidden></button>'
      + '<button type="button" data-a="fichier"></button><button type="button" data-a="fermer"></button></div></div>';
    document.body.appendChild(f); C.fond = f;
    const q = s => f.querySelector(s);
    q("h3").textContent = "📷 " + tx("camera", tj("engine/static/engine/js/capture_messagerie.js:151:48"));
    q("[data-m=photo]").textContent = "📸 " + tx("photo", "Photo");
    q("[data-m=video]").textContent = "🎬 " + tx("video", tj("engine/static/engine/js/capture_messagerie.js:153:59"));
    q("[data-a=basculer]").textContent = "🔄 " + tx("basculer", tj("engine/static/engine/js/capture_messagerie.js:154:65"));
    q("[data-a=utiliser]").textContent = "✔ " + tx("utiliser", "Utiliser");
    q("[data-a=reprendre]").textContent = "↺ " + tx("reprendre", "Reprendre");
    q("[data-a=fichier]").textContent = "📁 " + tx("fichier", tj("engine/static/engine/js/capture_messagerie.js:157:63"));
    q("[data-a=fermer]").textContent = "✕ " + tx("fermer", tj("engine/static/engine/js/capture_messagerie.js:158:61"));
    q("[data-m=photo]").onclick = () => changerMode("photo");
    q("[data-m=video]").onclick = () => changerMode("video");
    q("[data-a=declencher]").onclick = declencher;
    q("[data-a=basculer]").onclick = async () => { C.face = C.face === "environment" ? "user" : "environment"; await ouvrirFlux(); };
    q("[data-a=utiliser]").onclick = utiliserCapture;
    q("[data-a=reprendre]").onclick = reprendre;
    q("[data-a=fichier]").onclick = () => { fermerCamera(); champFichierOrigine(); };
    q("[data-a=fermer]").onclick = fermerCamera;
    f.addEventListener("keydown", ev => { if (ev.key === "Escape") fermerCamera(); });
    f.addEventListener("click", ev => { if (ev.target === f) fermerCamera(); });
  }
  const el = s => C.fond.querySelector(s);
  const videoDirect = () => C.fond.querySelector("#gy-cm-scene > video:not(#gy-cm-lecture)");
  function erreurCamera(m) { const e = el("#gy-cm-err"); e.textContent = m; e.style.display = m ? "block" : "none"; }
  function libererUrl() { if (C.url) { URL.revokeObjectURL(C.url); C.url = null; } }
  function arreterFlux() { if (C.flux) { C.flux.getTracks().forEach(t => t.stop()); C.flux = null; } }

  async function ouvrirFlux() {
    arreterFlux(); erreurCamera("");
    const video = C.mode === "video";
    const essais = [{ video: { facingMode: { ideal: C.face }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: video },
                    { video: true, audio: video }, ...(video ? [{ video: true, audio: false }] : [])];
    let flux = null, derniere = null;
    for (const c of essais) { try { flux = await navigator.mediaDevices.getUserMedia(c); break; } catch (e) { derniere = e; } }
    if (!flux) throw derniere || new Error("camera");
    C.flux = flux;
    const v = videoDirect(); v.srcObject = flux; v.hidden = false;
    const piste = flux.getVideoTracks()[0], reglage = piste && piste.getSettings ? piste.getSettings() : {};
    v.classList.toggle("miroir", reglage.facingMode === "user" || (!reglage.facingMode && C.face === "user"));
    try { const l = await navigator.mediaDevices.enumerateDevices(); C.nbCameras = l.filter(d => d.kind === "videoinput").length; } catch (_) { C.nbCameras = 2; }
    el("[data-a=basculer]").hidden = C.nbCameras < 2;
    try { await v.play(); } catch (_) { /* autoplay déjà actif */ }
  }
  function etatBoutons(apercu) {
    el("[data-a=declencher]").hidden = apercu; el("[data-a=basculer]").hidden = apercu || C.nbCameras < 2;
    el("[data-a=utiliser]").hidden = !apercu; el("[data-a=reprendre]").hidden = !apercu;
    el("#gy-cm-modes").hidden = apercu;
    el("[data-a=declencher]").textContent = C.mode === "photo" ? "📸 " + tx("capturer", "Capturer") : "● " + tx("demarrer", tj("engine/static/engine/js/capture_messagerie.js:196:125"));
  }
  async function changerMode(m) {
    if (C.rec && C.rec.state !== "inactive") return;
    C.mode = m;
    el("[data-m=photo]").classList.toggle("sel", m === "photo"); el("[data-m=video]").classList.toggle("sel", m === "video");
    reinitApercu(); etatBoutons(false);
    try { await ouvrirFlux(); } catch (e) { erreurCamera(tx("camera_refuse", tj("engine/static/engine/js/capture_messagerie.js:203:79"))); }
  }
  function reinitApercu() {
    libererUrl(); C.blob = null;
    const img = C.fond.querySelector("#gy-cm-scene img"), lec = el("#gy-cm-lecture");
    img.hidden = true; img.removeAttribute("src"); lec.hidden = true; lec.removeAttribute("src"); videoDirect().hidden = false;
  }
  function declencher() {
    if (C.mode === "photo") {
      const v = videoDirect(), cv = document.createElement("canvas");
      cv.width = v.videoWidth || 640; cv.height = v.videoHeight || 480;
      cv.getContext("2d").drawImage(v, 0, 0, cv.width, cv.height);
      cv.toBlob(b => { if (!b) return erreurCamera(tx("erreur", tj("engine/static/engine/js/capture_messagerie.js:215:66"))); finCapture(b, "image/jpeg", "jpg"); }, "image/jpeg", 0.92);
      return;
    }
    if (C.rec && C.rec.state !== "inactive") { C.rec.stop(); return; }   // second clic : on arrête
    const type = typeSupporte(["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm", "video/mp4"]);
    try { C.rec = type ? new MediaRecorder(C.flux, { mimeType: type }) : new MediaRecorder(C.flux); }
    catch (e) { return erreurCamera(tx("erreur", tj("engine/static/engine/js/capture_messagerie.js:221:51"))); }
    C.morceaux = [];
    C.rec.ondataavailable = ev => { if (ev.data && ev.data.size) C.morceaux.push(ev.data); };
    C.rec.onstop = () => {
      clearInterval(C.minuteur); C.minuteur = null; el("#gy-cm-rec").style.display = "none";
      const t = (C.rec && C.rec.mimeType) || "video/webm";
      if (C.morceaux.length) finCapture(new Blob(C.morceaux, { type: t }), t, extension(t.startsWith("video") ? t : "video/webm"));
      C.rec = null;
    };
    C.rec.start(); C.debut = Date.now();
    el("[data-a=declencher]").textContent = "■ " + tx("arreter", tj("engine/static/engine/js/capture_messagerie.js:231:67"));
    el("[data-a=basculer]").hidden = true; el("#gy-cm-modes").hidden = true;
    const r = el("#gy-cm-rec"); r.style.display = "flex";
    C.minuteur = setInterval(() => { el("#gy-cm-temps").textContent = mm((Date.now() - C.debut) / 1000); }, 300);
  }
  function finCapture(blob, type, ext) {
    C.blob = { blob, type, ext }; libererUrl(); C.url = URL.createObjectURL(blob);
    videoDirect().hidden = true;
    if (C.mode === "photo") { const img = C.fond.querySelector("#gy-cm-scene img"); img.src = C.url; img.hidden = false; }
    else { const l = el("#gy-cm-lecture"); l.src = C.url; l.hidden = false; }
    arreterFlux(); etatBoutons(true);
  }
  async function reprendre() {
    reinitApercu(); etatBoutons(false);
    try { await ouvrirFlux(); } catch (e) { erreurCamera(tx("camera_refuse", tj("engine/static/engine/js/capture_messagerie.js:245:79"))); }
  }
  function utiliserCapture() {
    if (!C.blob) return;
    const { blob, type, ext } = C.blob;
    const fichier = new File([blob], (C.mode === "photo" ? "photo_" : "video_") + Date.now() + "." + ext, { type });
    fermerCamera(); attacher(fichier);
  }
  function fermerCamera() {
    if (C.rec && C.rec.state !== "inactive") { C.rec.onstop = null; try { C.rec.stop(); } catch (_) { /* déjà arrêté */ } }
    C.rec = null; clearInterval(C.minuteur); C.minuteur = null;
    arreterFlux(); if (C.fond) { reinitApercu(); el("#gy-cm-rec").style.display = "none"; C.fond.classList.remove("on"); }
  }
  async function ouvrirCamera() {
    construireCamera(); C.mode = "photo"; reinitApercu(); etatBoutons(false);
    el("[data-m=photo]").classList.add("sel"); el("[data-m=video]").classList.remove("sel");
    C.fond.classList.add("on"); erreurCamera("");
    try { await ouvrirFlux(); el("[data-a=declencher]").focus(); }
    catch (e) {   // refus / aucune caméra : on bascule sur le champ fichier d'origine (caméra native du téléphone ou sélecteur)
      fermerCamera(); dire(tx("camera_refuse", tj("engine/static/engine/js/capture_messagerie.js:264:49")) + " " + tx("repli", tj("engine/static/engine/js/capture_messagerie.js:264:120")), true);
      champFichierOrigine();
    }
  }

  // ════════════ Branchement : phase de capture, avant les onclick d'origine ════════════
  document.addEventListener("click", ev => {
    const b = ev.target && ev.target.closest ? ev.target.closest("#msg-micro, #msg-saisie button") : null;
    if (!b) return;
    if (b.id === "msg-micro") {
      if (!apiMedia()) return;                        // sans API : l'ancien vocal() affiche son message
      ev.preventDefault(); ev.stopImmediatePropagation();
      if (V.rec) arreterVocal(false); else demarrerVocal();
      return;
    }
    if (/msg-camera/.test(b.getAttribute("onclick") || "")) {
      if (!apiMedia()) return;                        // sans API : champ fichier d'origine (caméra native sur mobile)
      ev.preventDefault(); ev.stopImmediatePropagation();
      ouvrirCamera();
    }
  }, true);
  window.addEventListener("pagehide", () => { try { if (V.rec) arreterVocal(true); fermerCamera(); } catch (_) { /* fermeture de page */ } });
})();
