/* CHARGEMENT GLOBAL — injecté dans toute page HTML par engine.middleware.ChargementMiddleware (un seul point, aucun gabarit à modifier).
   Dès qu'une requête (fetch / XMLHttpRequest) ou une navigation (clic sur un lien, envoi d'un formulaire) dure plus de DELAI_MS, un cercle
   animé (« serpent ») et un bouton Annuler s'affichent AU CENTRE de l'écran, l'interface derrière étant floue et sans focus ; ils disparaissent (et l'interface réapparaît, focus restitué) quand plus rien n'est en cours. Annuler abandonne les requêtes
   en cours (la connexion est coupée) et arrête la navigation. Les textes viennent des attributs data-* de la balise (déjà traduits par t()).
   Requêtes d'arrière-plan (sondages, statuts, notifications, observation…) : jamais signalées. Une requête peut aussi se déclarer silencieuse
   avec l'en-tête « X-Silencieux: 1 ». */
(function () {
  "use strict";
  // Une seule fois ; pas dans les cadres incorporés (cartes, aperçus…) SAUF le cadre de la coque : toutes les pages de l'application y
  // vivent, et le cercle de la coque (page parente) ne voit ni leurs navigations ni leurs requêtes.
  let cadreCoque = false;
  try { cadreCoque = !!(window.frameElement && window.frameElement.id === "coque-cadre"); } catch (_) { /* cadre d'une autre origine */ }
  if (window.__gyChargement || (window.self !== window.top && !cadreCoque)) return;
  window.__gyChargement = true;
  const ME = document.currentScript, TXT = { charge: (ME && ME.dataset.chargement) || "…", annuler: (ME && ME.dataset.annuler) || "×" };
  let DELAI_MS = 120, REPLI_NAVIGATION_MS = 15000;   // GYCHARG-PATCH-2 / GYCHARG-PATCH-3
  const NB = k => { const v = ME && ME.dataset[k]; return v === undefined || v === "" ? null : Number(v); };
  const PLAFOND_MS = NB("plafond") === null ? 45000 : NB("plafond");   // 0 = jamais de plafond : une requête lente garde le cercle (ancien comportement)
  const FENETRE_GESTE_MS = 12000, GESTES = NB("gestes") !== 0;          // 0 = toute requête est signalée, même sans action de l'utilisateur
  let dernierGeste = 0;
  { const d = NB("delai"); if (d !== null && d >= 0) DELAI_MS = d; }   // data-delai="0" : immédiat ; "350" : ancien comportement
  const FOND = /\/(statut|status|poll|notifications?|usabilite|fantome|heartbeat|ping|stream|langue)(\/|\?|$)|\.(js|css|png|jpe?g|svg|ico|woff2?)(\?|$)/i;
  const TELECHARGEMENT = /\/(telechargement|telecharger|download|export)|\.(zip|csv|pdf|xlsx?|docx?|pptx?|json|parquet)(\?|$)/i;
  const enCours = new Set();   // { annuler() } pour chaque requête signalée
  let navigation = false, minuteur = null, repli = null, carte = null;

  let saisieEnCours = false;
  let bouton = null, avant = null, compteur = null, debutAffichage = 0, tic = null;   // bouton Annuler ; élément qui avait le focus avant l'affichage
  function construire() {
    const st = document.createElement("style");
    st.textContent = "#gy-charg{position:fixed;inset:0;z-index:2147483000;display:none;align-items:center;justify-content:center;"
      + "background:rgba(8,12,20,.38);-webkit-backdrop-filter:blur(7px);backdrop-filter:blur(7px)}"   // tout ce qui est derrière devient flou
      + "#gy-charg.on{display:flex;animation:gy-charg-fondu .18s ease-out}"
      + "#gy-charg .gy-charg-boite{display:flex;flex-direction:column;align-items:center;gap:16px;padding:26px 34px;border-radius:18px;"
      + "background:var(--fond-info,#0f172a);color:var(--texte-clair,#e8e8e8);border:1px solid var(--accent,#38bdf8);"
      + "box-shadow:0 14px 48px rgba(0,0,0,.5);font:14px system-ui,sans-serif;max-width:86vw;text-align:center}"
      + "#gy-charg svg{width:56px;height:56px;animation:gy-charg-tour 1.1s linear infinite}"
      + "#gy-charg circle{fill:none;stroke:var(--accent,#38bdf8);stroke-width:2.8;stroke-linecap:round;stroke-dasharray:42 120;animation:gy-charg-serpent 1.4s ease-in-out infinite}"
      + "#gy-charg button{min-height:38px;padding:5px 22px;border-radius:999px;border:1px solid var(--accent,#38bdf8);background:transparent;color:inherit;cursor:pointer;font:inherit}"
      + "#gy-charg button:hover,#gy-charg button:focus-visible{background:var(--accent,#38bdf8);color:#000;outline:none}"
      + "@keyframes gy-charg-fondu{from{opacity:0}to{opacity:1}}@keyframes gy-charg-tour{to{transform:rotate(360deg)}}"
      + "@keyframes gy-charg-serpent{0%{stroke-dasharray:6 120}50%{stroke-dasharray:60 120}100%{stroke-dasharray:6 120}}"
      + "@media (prefers-reduced-motion:reduce){#gy-charg svg{animation-duration:3s}}";
    document.head.appendChild(st);
    carte = document.createElement("div"); carte.id = "gy-charg"; carte.setAttribute("role", "alertdialog"); carte.setAttribute("aria-modal", "true"); carte.setAttribute("aria-live", "polite");
    carte.innerHTML = '<div class="gy-charg-boite"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/></svg><span></span><small style="opacity:.75;font-variant-numeric:tabular-nums"></small><button type="button"></button></div>';
    const boite = carte.children[0]; boite.children[1].textContent = TXT.charge;
    compteur = boite.children[2]; bouton = boite.children[3]; bouton.textContent = TXT.annuler; bouton.onclick = annuler;
    (document.body || document.documentElement).appendChild(carte);   // script en tête de <head> : le corps n'existe pas encore
  }
  function afficher() {   // l'interface passe derrière (floue) et le focus va sur le bouton Annuler : le clavier ne peut plus agir sur la page masquée
    if (!carte) construire();
    avant = document.activeElement; carte.classList.add("on");
    // Quelqu'un est en train de TAPER dans un champ : le cercle s'affiche mais ne lui prend pas le focus (sinon chaque lettre attendrait la fin
    // de la requête). Hors saisie, le focus va sur « Annuler » comme avant.
    saisieEnCours = !!(avant && (/^(INPUT|TEXTAREA|SELECT)$/.test(avant.tagName) || avant.isContentEditable));
    if (!saisieEnCours) bouton.focus();
    debutAffichage = Date.now(); majCompteur(); clearInterval(tic); tic = setInterval(majCompteur, 500);
  }
  function majCompteur() {   // temps écoulé / délai maximal : l'attente est bornée et visible (le cercle se libère de lui-même au délai)
    const limite = navigation ? REPLI_NAVIGATION_MS : PLAFOND_MS, s = Math.floor((Date.now() - debutAffichage) / 1000);
    compteur.textContent = s + " s" + (limite > 0 ? " / " + Math.round(limite / 1000) + " s" : "");
  }
  function masquer() {
    clearInterval(tic); tic = null;
    carte.classList.remove("on");
    if (avant && avant.focus && document.contains(avant)) { try { avant.focus(); } catch (_) { /* élément disparu */ } }
    avant = null;
  }
  // Animation d'entrée (#intro-overlay, pages index/client) : plus d'exception — le cercle s'affiche aussi pendant l'animation (z-index maximal,
  // au-dessus d'elle). Ancien comportement (cercle retenu tant que l'animation est à l'écran) : supprimé, ce chargement paraissait alors silencieux.
  // Ancien commentaire : le cercle ne la touche jamais. data-intro=1 est posé par le middleware ; l'arrivée par
  // « ← Retour à {table} » (?retour=1) supprime l'animation, donc rien à protéger. Tant que l'animation est à l'écran — ou que l'analyseur HTML
  // n'a pas encore atteint son élément — on n'affiche rien ; on réessaie ensuite, pour les requêtes encore en cours une fois l'animation partie.
  const INTRO = !!(ME && ME.dataset.intro) && !/[?&]retour=1(&|$)/.test(location.search);
  let introVue = false, attenteIntro = null;
  document.addEventListener("DOMContentLoaded", () => { introVue = true; });   // l'animation dure bien plus longtemps que le DOM : absente ici = déjà retirée
  function sousIntro() {
    const presente = !!document.getElementById("intro-overlay");
    if (presente) introVue = true;
    return presente || (INTRO && !introVue);
  }
  function maj(immediat) {   // affiche (après un court délai, pour ne pas clignoter sur les requêtes rapides) ou masque selon ce qui est en cours
    const actif = enCours.size > 0 || navigation;
    if (!actif) { clearTimeout(minuteur); minuteur = null; if (carte && carte.classList.contains("on")) masquer(); return; }
    if (carte && carte.classList.contains("on")) return;
    if (immediat) { clearTimeout(minuteur); minuteur = null; afficher(); return; }   // navigation / chargement de page : aucun délai
    if (minuteur) return;
    minuteur = setTimeout(() => { minuteur = null; if (enCours.size || navigation) afficher(); }, DELAI_MS);
  }
  function geste() { dernierGeste = Date.now(); }
  ["pointerdown", "keydown", "submit", "change", "touchstart"].forEach(n => document.addEventListener(n, geste, true));
  function deLUtilisateur() {   // sans navigator.userActivation ni suivi des gestes : on signale tout, comme avant
    if (!GESTES) return true;
    if (enCours.size || navigation) return true;
    if (Date.now() - dernierGeste < FENETRE_GESTE_MS) return true;
    try { if (navigator.userActivation && navigator.userActivation.isActive) return true; } catch (_) { /* absent */ }
    return performance.now() < 10000;   // chargement initial de la page : signalé ; ensuite seuls les gestes comptent
  }
  function suivre(annuler) {
    const e = { annuler }; enCours.add(e); maj();
    let plafond = null;
    if (PLAFOND_MS > 0) plafond = setTimeout(() => { enCours.delete(e); maj(); }, PLAFOND_MS);   // la requête continue, seul l'écran est libéré
    return () => { clearTimeout(plafond); enCours.delete(e); maj(); };
  }
  function annuler() {
    for (const e of [...enCours]) { try { e.annuler(); } catch (_) { /* déjà terminée */ } }
    if (navigation) { try { window.stop(); } catch (_) { /* rien à arrêter */ } }
    finirNavigation();
  }
  function debutNavigation() { navigation = true; clearTimeout(repli); repli = setTimeout(finirNavigation, REPLI_NAVIGATION_MS); maj(true); }   // repli : un téléchargement ne décharge pas la page
  function finirNavigation() { navigation = false; clearTimeout(repli); maj(); }

  // ── fetch : chaque appel reçoit son AbortController, relié au signal éventuel de l'appelant ──
  const fetchOrigine = window.fetch;
  window.fetch = function (entree, init) {
    const url = typeof entree === "string" ? entree : (entree && entree.url) || "";
    const silencieux = (init && (init.keepalive || (init.headers && (new Headers(init.headers)).get("X-Silencieux")))) || FOND.test(url) || entree instanceof Request;
    if (silencieux || !deLUtilisateur()) return fetchOrigine.apply(this, arguments);
    const ctl = new AbortController(), amont = init && init.signal;
    if (amont) { if (amont.aborted) ctl.abort(); else amont.addEventListener("abort", () => ctl.abort(), { once: true }); }
    const fin = suivre(() => ctl.abort());
    try { return fetchOrigine.call(this, entree, Object.assign({}, init, { signal: ctl.signal })).finally(fin); }
    catch (err) { fin(); throw err; }
  };
  // ── XMLHttpRequest ──
  const ouvrir = XMLHttpRequest.prototype.open, envoyer = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, url) { this.__gyUrl = String(url || ""); return ouvrir.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function () {
    if (!FOND.test(this.__gyUrl || "") && deLUtilisateur()) { const fin = suivre(() => this.abort()); this.addEventListener("loadend", fin, { once: true }); }
    return envoyer.apply(this, arguments);
  };
  // ── Navigation : clic sur un lien interne ou envoi d'un formulaire (hors téléchargements, nouvel onglet, simple ancre) ──
  document.addEventListener("click", e => {
    const a = e.target.closest && e.target.closest("a[href]");
    if (!a || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || a.hasAttribute("download") || (a.target && a.target !== "_self")) return;
    const brut = a.getAttribute("href") || "";
    if (brut.charAt(0) === "#") return;   // href="#", "#ancre" : même page, aucune navigation
    const u = new URL(a.href, location.href);
    if (u.origin !== location.origin || !/^https?:$/.test(u.protocol) || TELECHARGEMENT.test(u.pathname) || (u.pathname === location.pathname && u.search === location.search && u.hash)) return;
    setTimeout(() => { if (!e.defaultPrevented) debutNavigation(); }, 0);   // après les autres écouteurs : s'ils ont repris la main (SPA), pas de chargement de page
  });
  document.addEventListener("submit", e => {
    const f = e.target;
    if (f && !(f.target && f.target !== "_self") && !TELECHARGEMENT.test(f.action || "")) setTimeout(() => { if (!e.defaultPrevented) debutNavigation(); }, 0);
  });
  window.addEventListener("beforeunload", () => { if (!navigation) debutNavigation(); });   // navigation par script (location.href = …, bouton sans lien) : le cercle apparaît aussi
  window.addEventListener("pageshow", finirNavigation);   // retour arrière (cache de navigation) : la page revient, plus de chargement
  window.addEventListener("hashchange", finirNavigation);
  window.addEventListener("popstate", finirNavigation);
  document.addEventListener("keydown", e => {
    if (!carte || !carte.classList.contains("on")) return;
    if (e.key === "Escape" && !saisieEnCours) annuler();
    else if (e.key === "Tab" && !saisieEnCours) { e.preventDefault(); bouton.focus(); }   // le focus reste sur le chargement tant qu'il dure
  }, true);
  // Démarrage : le script est exécuté dès l'ouverture de <head> (ChargementMiddleware) — le cercle couvre le chargement de la page elle-même.
  if (document.readyState !== "complete") { debutNavigation(); window.addEventListener("load", finirNavigation); }
  document.addEventListener("DOMContentLoaded", () => { if (carte && document.body && carte.parentNode !== document.body) document.body.appendChild(carte); });
})();
