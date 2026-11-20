// MODE LIVREUR de la carte de géolocalisation : un écran simple pour un livreur seul sur la route (téléphone, une main).
// Rien n'est recalculé ici : tout se branche sur l'existant (window.GEO_API de geolocalisation.js, window.GEO_ITI de geo_outils.js pour la position,
// la destination et le tracé par les routes, window.GEO_LOG de logistique.js pour les arrêts de la tournée).
// Ce que le mode ajoute : GPS continu (watchPosition), carte qui suit, prochaine manœuvre en gros (consignes pas à pas du serveur) avec voix en option,
// reste / heure d'arrivée, écran maintenu allumé, recalcul quand on s'écarte du tracé, choix de la destination (adresse, point sur la carte, arrêts de la tournée),
// bouton « Livraison faite » qui passe à l'arrêt suivant. Le panneau d'analyse est masqué pendant le mode ; « Quitter » le rétablit.
// Chargé APRÈS geo_outils.js et logistique.js. Adresse de la page + #livreur = ouvre directement ce mode (raccourci écran d'accueil).
(function () {
"use strict";
const A = window.GEO_API, I = window.GEO_ITI, G = window.GEO;
if (!A || !I || !document.getElementById("zone-carte")) return;
const $ = id => document.getElementById(id), el = A.el, S = A.S;
const URL_ADR = "/api/tables/" + G.tableId + "/geolocalisation/adresse/";
const L = {actif: false, suivre: true, watch: null, minuteur: null, wake: null, voix: false, derniereMaj: 0, pos: null, precision: null, cap: null, vitesse: null,
  erreurGps: "", avis: null, parle: {}, candidat: null, arrets: null, prochain: 0, cleArrivee: null, choix: false, sa: 0};
const N = {};   // noeuds de l'interface
const GLYPHE = {"uturn": "↩", "sharp right": "↘", "right": "➡", "slight right": "↗", "straight": "⬆", "slight left": "↖", "left": "⬅", "sharp left": "↙"};
const glyphe = e => !e ? "📍" : e.type === "arrive" ? "🏁" : /roundabout|rotary/.test(e.type) ? "⟳" : GLYPHE[e.modifier] || "⬆";
const heure = s => new Date(Date.now() + s * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
const cap = (a, b, c, d) => { const p1 = a * Math.PI / 180, p2 = c * Math.PI / 180, dl = (d - b) * Math.PI / 180; return (Math.atan2(Math.sin(dl) * Math.cos(p2), Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl)) * 180 / Math.PI + 360) % 360; };
const arrondi = (m, pas) => Math.max(pas, Math.round(m / pas) * pas);

// ── Interface (créée une fois ; masquée tant que le mode n'est pas actif) ───────────────────────────────
function construire() {
  const zone = $("zone-carte");
  N.ouvrir = el("button", {id: "lv-ouvrir", class: "geo-btn", title: tj("engine/static/engine/js/geo_livreur.js:26:53"), onclick: activer}, tj("engine/static/engine/js/geo_livreur.js:lbl_mode_livreur"));
  N.fleche = el("div", {class: "lv-fleche"}, "📍");
  N.dist = el("div", {class: "lv-dist"}, "");
  N.texte = el("div", {class: "lv-texte"}, "");
  N.puis = el("div", {class: "lv-puis"}, "");
  N.etat = el("div", {class: "lv-etat"}, "");
  N.haut = el("div", {id: "lv-haut"}, N.fleche, el("div", {class: "lv-col"}, N.dist, N.texte, N.puis, N.etat));
  N.resume = el("div", {class: "lv-resume"}, "");
  N.dest = el("div", {class: "lv-dest"}, "");
  N.fait = el("button", {class: "lv-gros lv-ok", onclick: livraisonFaite}, tj("engine/static/engine/js/geo_livreur.js:lbl_livraison_faite"));
  N.fait.style.display = "none";
  N.choix = el("div", {class: "lv-choix"});
  N.choix.style.display = "none";
  N.voix = el("button", {class: "lv-btn", title: tj("engine/static/engine/js/geo_livreur.js:39:51"), onclick: () => { L.voix = !L.voix; if (!L.voix && window.speechSynthesis) window.speechSynthesis.cancel(); majBoutons(); }}, "");
  N.recentrer = el("button", {class: "lv-btn", title: tj("engine/static/engine/js/geo_livreur.js:40:56"), onclick: () => { L.suivre = true; if (L.pos) suivre(L.pos.lat, L.pos.lon); majBoutons(); }}, "◎ Recentrer");
  N.mode = el("select", {class: "lv-btn", title: tj("engine/static/engine/js/geo_livreur.js:41:51"), onchange: () => { const s = $("ot-mode-route"); if (s) { s.value = N.mode.value; s.dispatchEvent(new Event("change")); } }},
    ...[["couloir", tj("engine/static/engine/js/geo_livreur.js:42:22")], ["raccourcis", "⚡ Raccourcis"], ["moto", "🏍️ Moto"], ["bateau", "🚤 Bateau"], ["voiture", "🚗 Voiture"], ["velo", tj("engine/static/engine/js/geo_livreur.js:42:158")], ["pied", tj("engine/static/engine/js/geo_livreur.js:42:178")]].map(([k, v]) => el("option", {value: k}, v)));
  N.lieu = el("button", {class: "lv-btn", onclick: () => { L.choix = !L.choix; majChoix(); }}, "📍 Destination");
  N.quitter = el("button", {class: "lv-btn lv-sortie", onclick: desactiver}, "✕ Quitter");
  N.bas = el("div", {id: "lv-bas"}, N.resume, N.dest, N.fait, N.choix, el("div", {class: "lv-actions"}, N.lieu, N.recentrer, N.voix, N.mode, N.quitter));
  zone.append(N.haut, N.bas);
  // Sidebar : « Mode livreur » et affichage/masquage de la barre de lecture (en bas de la carte), pour libérer la vue
  const bloc = document.querySelector("#sidebar .geo-bloc");
  const barre = $("geo-barre-lecture");
  let btnBarre = null;
  const cle = "geo_barre_lecture_masquee";
  const appliquerBarre = masquee => {
    if (barre) barre.style.display = masquee ? "none" : "";
    if (btnBarre) btnBarre.textContent = masquee ? tj("engine/static/engine/js/geo_livreur.js:54:53") : tj("engine/static/engine/js/geo_livreur.js:54:88");
    try { localStorage.setItem(cle, masquee ? "1" : "0"); } catch (e) { /* stockage indisponible */ }
  };
  if (barre) {
    btnBarre = el("button", {type: "button", class: "geo-btn", id: "btn-barre-lecture", onclick: () => appliquerBarre(barre.style.display !== "none")}, "");
    const x = el("button", {type: "button", id: "lec-masquer", title: tj("engine/static/engine/js/geo_livreur.js:59:72"), "aria-label": tj("engine/static/engine/js/geo_livreur.js:59:117"), onclick: () => appliquerBarre(true)}, "✕");
    barre.append(x);
    let m = false; try { m = localStorage.getItem(cle) === "1"; } catch (e) { /* ignoré */ }
    appliquerBarre(m);
    // Exception : l'animation. Lancer la lecture (▶) ou les trajectoires de test réaffiche la barre, sans changer votre préférence ;
    // masquer la barre n'arrête jamais l'animation en cours.
    const montrer = () => { barre.style.display = ""; if (btnBarre) btnBarre.textContent = tj("engine/static/engine/js/geo_livreur.js:65:93"); };
    const play = $("lec-play"); if (play) play.addEventListener("click", montrer);
    const lat = $("geo-vide-lateral"); if (lat) lat.addEventListener("click", e => { if (e.target.closest("button")) montrer(); });
  }
  if (bloc) bloc.append(N.ouvrir, ...(btnBarre ? [btnBarre] : [])); else zone.append(N.ouvrir);
  majBoutons();
}
function majBoutons() {
  N.voix.textContent = L.voix ? "🔊 Voix" : "🔇 Voix";
  N.recentrer.classList.toggle("lv-alerte", !L.suivre);
  const m = I.modeActuel(); if (m) N.mode.value = m;
}

// ── GPS continu, écran allumé, suivi de la carte ──────────────────────────────────────────────────
function surPosition(p) {
  const c = p.coords, maintenant = Date.now();
  L.precision = c.accuracy; L.cap = Number.isFinite(c.heading) ? c.heading : null; L.vitesse = Number.isFinite(c.speed) ? c.speed : null; L.erreurGps = "";
  if (c.accuracy > 200) { L.erreurGps = tj("engine/static/engine/js/geo_livreur.js:lbl_gps_faible") + Math.round(c.accuracy) + tj("engine/static/engine/js/geo_livreur.js:59:92"); return; }   // position approximative (Wi-Fi) : ne pas faire sauter le tracé
  const avant = L.pos, bouge = avant ? A.hav(avant.lat, avant.lon, c.latitude, c.longitude) : Infinity;
  if (avant && (maintenant - L.derniereMaj < 2000 || (bouge < 4 && maintenant - L.derniereMaj < 10000))) return;   // au plus une mise à jour toutes les 2 s, et seulement si l'on a bougé (ou toutes les 10 s)
  L.pos = {lat: c.latitude, lon: c.longitude}; L.derniereMaj = maintenant;
  I.position(c.latitude, c.longitude, tj("engine/static/engine/js/geo_livreur.js:63:40"), true);
  if (!L.aff) { L.aff = {lat: c.latitude, lon: c.longitude}; if (L.suivre) suivre(c.latitude, c.longitude); }
  afficher();
}
function surErreur(e) {
  L.erreurGps = e.code === 1 ? tj("engine/static/engine/js/geo_livreur.js:68:33") :
    tj("engine/static/engine/js/geo_livreur.js:69:6");
  afficher();
}
function suivre(lat, lon) { A.centrerSur(lat, lon, 16); }
async function veille() {
  try { if (navigator.wakeLock && document.visibilityState === "visible") L.wake = await navigator.wakeLock.request("screen"); } catch (e) { L.wake = null; }
}
document.addEventListener("visibilitychange", () => { if (L.actif && document.visibilityState === "visible") veille(); });
function demarrerGps() {
  if (!navigator.geolocation) { L.erreurGps = tj("engine/static/engine/js/geo_livreur.js:78:48"); return; }
  L.watch = navigator.geolocation.watchPosition(surPosition, surErreur, {enableHighAccuracy: true, maximumAge: 2000, timeout: 20000});
}

// ── Activation / sortie ───────────────────────────────────────────────────────────────────────
function activer() {
  if (L.actif) return;
  L.actif = true; L.suivre = true; L.erreurGps = ""; L.choix = !I.etat().dest;
  document.body.classList.add("livreur", "plein-ecran");
  I.livreur(true); I.demanderNotifications();
  { const c = $("ot-route-actif"); if (c && !c.checked) { c.checked = true; c.dispatchEvent(new Event("change")); } }   // le tracé par les routes est la base du mode livreur
  demarrerGps(); veille();
  L.minuteur = setInterval(afficher, 1000);
  try { history.replaceState(null, "", "#livreur"); } catch (e) { /* sans effet */ }
  majBoutons(); majChoix(); afficher();
}
function desactiver() {
  L.actif = false;
  if (L.watch != null && navigator.geolocation) navigator.geolocation.clearWatch(L.watch);
  L.watch = null; clearInterval(L.minuteur); L.minuteur = null;
  try { if (L.wake) L.wake.release(); } catch (e) { /* déjà relâché */ }
  L.wake = null;
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  document.body.classList.remove("livreur", "plein-ecran");
  I.livreur(false);
  try { history.replaceState(null, "", location.pathname + location.search); } catch (e) { /* sans effet */ }
  A.redessiner();
}

// ── Progression le long de l'itinéraire (rien n'est demandé au serveur : tout vient du tracé déjà calculé) ──
function preparer(rt) {
  if (rt.lv) return rt.lv;
  const g = rt.geometrie, cum = [0], s = [];
  for (let i = 1; i < g.length; i++) cum.push(cum[i - 1] + A.hav(g[i - 1][0], g[i - 1][1], g[i][0], g[i][1]));
  let j0 = 0;
  (rt.etapes || []).forEach(e => {       // position de chaque manœuvre le long du tracé : premier sommet à moins de 20 m, sinon le plus proche en avant
    let meilleur = j0, bd = Infinity, premier = -1;
    for (let j = j0; j < g.length; j++) { const d = A.hav(g[j][0], g[j][1], e.lat, e.lon); if (d < bd) { bd = d; meilleur = j; } if (d <= 20) { premier = j; break; } }
    j0 = premier >= 0 ? premier : meilleur; s.push(cum[j0]);
  });
  return (rt.lv = {cum, s, total: cum[cum.length - 1] || 0});
}
function parler(cle, texte) {
  if (!L.voix || L.parle[cle] || !window.speechSynthesis) return;
  L.parle[cle] = true;
  try { window.speechSynthesis.cancel(); const u = new SpeechSynthesisUtterance(texte); u.lang = (window.I18N_LOCALE||"fr-FR"); window.speechSynthesis.speak(u); } catch (e) { /* voix indisponible */ }
}
const minuscule = t => t.charAt(0).toLowerCase() + t.slice(1);

function afficher() {
  if (!L.actif) return;
  const T = I.etat(), rt = T.route, dep = T.depart, dest = T.dest;
  const avis = L.avis && Date.now() < L.avis.jusqua ? L.avis.texte : "";
  let fleche = "📍", dist = "", texte = "", puis = "", etat = L.erreurGps, resume = "", arrive = false, rot = "";
  if (!L.pos && !L.erreurGps) etat = tj("engine/static/engine/js/geo_livreur.js:132:39");
  else if (L.precision && L.precision > 50 && !L.erreurGps) etat = tj("engine/static/engine/js/geo_livreur.js:133:69") + Math.round(L.precision) + " m)";
  if (!dest) texte = tj("engine/static/engine/js/geo_livreur.js:134:23");
  else if (!dep) texte = tj("engine/static/engine/js/geo_livreur.js:position_inconnue");
  else {
    const droite = A.hav(dep.lat, dep.lon, dest.lat, dest.lon);
    arrive = droite <= 40 && !!L.pos;
    if (rt && rt.ok && rt.geometrie && rt.geometrie.length > 1) {
      const lv = preparer(rt), pr = I.surTrace(rt.geometrie, dep.lat, dep.lon), sPos = pr.total > 0 ? pr.s * lv.total / pr.total : 0, reste = Math.max(0, lv.total - sPos);
      const part = lv.total > 0 ? reste / lv.total : 0, sec = (rt.duree_s || 0) * part;
      let i = 1; while (i < lv.s.length - 1 && lv.s[i] <= sPos + 8) i++;
      const e = rt.etapes && rt.etapes[i];
      if (e) {
        const avant = Math.max(0, lv.s[i] - sPos);
        fleche = glyphe(e); dist = A.fmtM(avant < 1000 ? arrondi(avant, 10) : avant); texte = e.texte;
        const suiv = rt.etapes[i + 1];
        if (suiv && e.type !== "arrive" && lv.s[i + 1] - lv.s[i] < 250) puis = "Puis : " + minuscule(suiv.texte);
        const cle = rt.t + "-" + i;
        if (e.type === "arrive") { /* l'arrivée est annoncée une seule fois plus bas */ } else if (avant <= 80) parler(cle + "-p", e.texte); else if (avant <= 350) parler(cle + "-m", tj("engine/static/engine/js/geo_livreur.js:lbl_dans_n", {v1: arrondi(avant, 50)}) + tj("engine/static/engine/js/geo_livreur.js:173:216") + minuscule(e.texte));
      } else { fleche = "⬆"; dist = A.fmtM(reste); texte = tj("engine/static/engine/js/geo_livreur.js:151:61"); }
      if (pr.d > 50) etat = tj("engine/static/engine/js/geo_livreur.js:152:30") + Math.round(pr.d) + tj("engine/static/engine/js/geo_livreur.js:metres_recalcul");
      resume = A.fmtM(reste) + " · " + (sec >= 60 ? A.fmtDuree(sec) + tj("engine/static/engine/js/geo_livreur.js:153:72") + heure(sec) : tj("engine/static/engine/js/geo_livreur.js:153:106"));
      if (reste <= 25) arrive = !!L.pos;
    } else {   // pas de tracé par les routes (calcul en cours, réseau absent, mer…) : la ligne droite reste le guide
      const b = cap(dep.lat, dep.lon, dest.lat, dest.lon);
      fleche = "⬆"; dist = A.fmtM(droite); texte = tj("engine/static/engine/js/geo_livreur.js:157:53") + I.fmtCap(b);
      rot = "rotate(" + Math.round(L.cap != null && (L.vitesse || 0) > 1 ? b - L.cap : b) + "deg)";
      const msg = $("ot-route") ? $("ot-route").textContent : "";
      etat = etat || (msg ? msg.replace(/\s+/g, " ").slice(0, 110) : tj("engine/static/engine/js/geo_livreur.js:160:71"));
      resume = A.fmtM(droite) + tj("engine/static/engine/js/geo_livreur.js:161:34");
    }
    if (arrive) { fleche = "🏁"; dist = ""; texte = tj("engine/static/engine/js/geo_livreur.js:163:53"); puis = ""; rot = ""; }
  }
  if (avis) etat = avis;   // un changement d'itinéraire reste affiché 10 s en haut de l'écran
  N.fleche.style.transform = rot; N.fleche.textContent = fleche; N.dist.textContent = dist; N.texte.textContent = texte; N.puis.textContent = puis; N.etat.textContent = etat;
  N.etat.style.display = etat ? "block" : "none"; N.puis.style.display = puis ? "block" : "none";
  N.resume.textContent = resume;
  N.dest.textContent = dest ? tj("engine/static/engine/js/geo_livreur.js:169:32") + (dest.nom || dest.lat.toFixed(5) + ", " + dest.lon.toFixed(5)) + (L.arrets ? tj("engine/static/engine/js/geo_livreur.js:192:163") + (L.prochain + 1) + "/" + L.arrets.length + ")" : "") : "";
  const cleArrivee = arrive && dest ? dest.lat + "," + dest.lon : null;
  if (cleArrivee && cleArrivee !== L.cleArrivee) { L.cleArrivee = cleArrivee; parler("arr-" + cleArrivee, tj("engine/static/engine/js/geo_livreur.js:171:108")); }
  if (!cleArrivee) L.cleArrivee = null;
  N.fait.style.display = arrive ? "block" : "none";
  N.fait.textContent = L.arrets && L.prochain < L.arrets.length - 1 ? tj("engine/static/engine/js/geo_livreur.js:174:72") : tj("engine/static/engine/js/geo_livreur.js:lbl_livraison_faite");
  majBoutons();
}

// ── Destination : adresse, point sur la carte, arrêts de la tournée ─────────────────────────────────
function viser(lat, lon, nom) {
  L.candidat = null; L.choix = false; L.cleArrivee = null; L.parle = {};
  I.destination(lat, lon, nom); L.suivre = true; if (L.pos) suivre(L.pos.lat, L.pos.lon);
  majChoix(); afficher();
}
function livraisonFaite() {
  A.toast(tj("engine/static/engine/js/geo_livreur.js:185:12"));
  I.effacerDestination(); L.parle = {}; L.cleArrivee = null;
  if (L.arrets && L.prochain < L.arrets.length - 1) { L.prochain++; const e = L.arrets[L.prochain]; viser(e.lat, e.lon, e.nom); }
  else { if (L.arrets) A.toast(tj("engine/static/engine/js/geo_livreur.js:188:33")); L.arrets = null; L.prochain = 0; L.choix = true; majChoix(); afficher(); }
}
async function chercher(q) {
  const z = N.resultats; z.textContent = "";
  if (q.length < 3) { A.toast(tj("engine/static/engine/js/geo_livreur.js:192:32"), true); return; }
  const n = ++L.sa; z.append(el("div", {class: "lv-note"}, tj("engine/static/engine/js/geo_livreur.js:193:61")));
  try {
    const rep = await fetch(URL_ADR + "?q=" + encodeURIComponent(q)), d = await rep.json().catch(() => ({}));
    if (n !== L.sa) return;
    z.textContent = "";
    if (!rep.ok) { z.append(el("div", {class: "lv-note"}, d.erreur || tj("engine/static/engine/js/geo_livreur.js:198:72"))); return; }
    if (!d.resultats.length) { z.append(el("div", {class: "lv-note"}, tj("engine/static/engine/js/geo_livreur.js:199:72"))); return; }
    d.resultats.slice(0, 6).forEach(r => z.append(el("button", {class: "lv-gros", title: r.nom, onclick: () => viser(r.lat, r.lon, r.nom.split(",").slice(0, 2).join(","))}, r.nom.split(",").slice(0, 3).join(","))));
  } catch (e) { if (n === L.sa) { z.textContent = ""; z.append(el("div", {class: "lv-note"}, tj("engine/static/engine/js/geo_livreur.js:201:95"))); } }
}
function majChoix() {
  const c = N.choix; c.style.display = L.choix || L.candidat ? "block" : "none";
  c.textContent = "";
  if (!(L.choix || L.candidat)) return;
  if (L.candidat) {
    c.append(el("div", {class: "lv-note"}, tj("engine/static/engine/js/geo_livreur.js:208:45") + L.candidat.lat.toFixed(5) + ", " + L.candidat.lon.toFixed(5)),
      el("button", {class: "lv-gros lv-ok", onclick: () => viser(L.candidat.lat, L.candidat.lon, null)}, tj("engine/static/engine/js/geo_livreur.js:lbl_livrer_ici")),
      el("button", {class: "lv-btn", onclick: () => { L.candidat = null; majChoix(); A.redessiner(); }}, tj("engine/static/engine/js/geo_livreur.js:210:107")));
    return;
  }
  N.champ = el("input", {type: "search", class: "lv-champ", placeholder: tj("engine/static/engine/js/geo_livreur.js:213:75"), autocomplete: "off", enterkeyhint: "search"});
  N.champ.addEventListener("keydown", e => { if (e.key === "Enter") chercher(N.champ.value.trim()); });
  N.resultats = el("div", {class: "lv-resultats"});
  c.append(el("div", {class: "lv-ligne"}, N.champ, el("button", {class: "lv-btn", onclick: () => chercher(N.champ.value.trim())}, "🔍")), N.resultats);
  const plan = window.GEO_LOG && window.GEO_LOG.plan();
  if (plan && !plan.vide && plan.routes && plan.routes.length) {   // tournée calculée dans l'analyse : choisir son véhicule puis enchaîner les arrêts
    const sel = el("select", {class: "lv-btn", title: tj("engine/static/engine/js/geo_livreur.js:219:56")}, el("option", {value: ""}, tj("engine/static/engine/js/geo_livreur.js:219:97")), ...plan.routes.map((r, i) => el("option", {value: String(i)}, r.nom + " (" + r.etapes.length + tj("engine/static/engine/js/geo_livreur.js:242:285"))));
    sel.onchange = () => { const r = plan.routes[parseInt(sel.value, 10)]; if (r && r.etapes.length) { L.arrets = r.etapes.map(e => ({lat: e.lat, lon: e.lon, nom: e.nom || (tj("engine/static/engine/js/geo_livreur.js:220:175") + e.ordre)})); L.prochain = 0; viser(L.arrets[0].lat, L.arrets[0].lon, L.arrets[0].nom); } };
    c.append(sel);
  }
}

// ── Branchements sur la carte ──────────────────────────────────────────────────────────────────
document.addEventListener("geo-alerte-zone", ev => {   // un objet ou vous-même approche une zone stratégique / de danger : message en haut de l'écran + annonce vocale
  const d = ev.detail || {}; if (!L.actif) return;
  L.avis = {texte: d.message, jusqua: Date.now() + 15000}; parler("zone-" + Date.now(), d.voix || d.message); afficher();
});
document.addEventListener("geo-itineraire", ev => {   // avis de geo_outils.js : message en haut de l'écran + annonce vocale
  const d = ev.detail || {}; if (!L.actif || d.type === "nouveau") return;
  L.avis = {texte: d.message, jusqua: Date.now() + 10000}; parler("avis-" + Date.now(), d.voix || d.message); afficher();
});
(window.GEO_CLIC = window.GEO_CLIC || []).push((px, py) => {   // un appui sur la carte propose un point ; la destination ne change qu'après confirmation (pas d'appui accidentel en roulant)
  if (!L.actif) return false;
  const [lat, lon] = A.versGeo(px, py); L.candidat = {lat, lon}; majChoix(); A.redessiner(); return true;
});
(window.GEO_IMAGE = window.GEO_IMAGE || []).push(dt => {   // à chaque image : la position affichée (et la carte qui suit) rattrape le dernier point GPS, constante de temps 0,6 s
  if (!L.actif || !L.pos) { L.aff = null; return false; }
  if (!L.aff) L.aff = {lat: L.pos.lat, lon: L.pos.lon};
  const dla = L.pos.lat - L.aff.lat, dlo = L.pos.lon - L.aff.lon;
  if (Math.abs(dla) + Math.abs(dlo) < 1e-8) return false;
  const f = 1 - Math.exp(-dt / 0.6); L.aff.lat += dla * f; L.aff.lon += dlo * f;
  if (L.suivre) suivre(L.aff.lat, L.aff.lon);
  return true;
});
(window.GEO_DESSIN = window.GEO_DESSIN || []).push(ctx => {   // position bien visible (cercle de précision, pointe de direction) et point choisi
  if (!L.actif) return;
  if (L.pos) {
    const [x, y] = A.versEcran((L.aff || L.pos).lat, (L.aff || L.pos).lon);
    if (L.precision) { const r = L.precision / A.mParPx((L.aff || L.pos).lat); if (r > 12) { ctx.fillStyle = gyCouleur("--forme-bleu", "#3aa0ff", 0.15); ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.fill(); } }
    ctx.save(); ctx.translate(x, y); if (L.cap != null && (L.vitesse || 0) > 1) ctx.rotate(L.cap * Math.PI / 180);
    ctx.fillStyle = gyCouleur("--forme-bleu", "#3aa0ff"); ctx.strokeStyle = gyCouleur("--forme-clair", "#fff"); ctx.lineWidth = 3;
    ctx.beginPath(); if (L.cap != null && (L.vitesse || 0) > 1) { ctx.moveTo(0, -16); ctx.lineTo(11, 12); ctx.lineTo(0, 6); ctx.lineTo(-11, 12); ctx.closePath(); } else ctx.arc(0, 0, 10, 0, 2 * Math.PI);
    ctx.fill(); ctx.stroke(); ctx.restore();
  }
  if (L.candidat) { const [x, y] = A.versEcran(L.candidat.lat, L.candidat.lon); ctx.strokeStyle = gyCouleur("--forme-jaune", "#ffd23f"); ctx.lineWidth = 3; ctx.beginPath(); ctx.arc(x, y, 12, 0, 2 * Math.PI); ctx.moveTo(x - 18, y); ctx.lineTo(x + 18, y); ctx.moveTo(x, y - 18); ctx.lineTo(x, y + 18); ctx.stroke(); }
});
{ const cv = $("carte"), p0 = {x: 0, y: 0, bas: false};    // faire glisser ou zoomer la carte suspend le suivi : « Recentrer » le reprend
  cv.addEventListener("pointerdown", e => { p0.x = e.clientX; p0.y = e.clientY; p0.bas = true; });
  cv.addEventListener("pointermove", e => { if (L.actif && p0.bas && L.suivre && Math.hypot(e.clientX - p0.x, e.clientY - p0.y) > 12) { L.suivre = false; majBoutons(); } });
  cv.addEventListener("pointerup", () => { p0.bas = false; }); cv.addEventListener("pointercancel", () => { p0.bas = false; });
  cv.addEventListener("wheel", () => { if (L.actif && L.suivre) { L.suivre = false; majBoutons(); } }, {passive: true}); }
document.addEventListener("keydown", e => { if (e.key === "Escape" && L.actif && !/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) desactiver(); });

construire();
window.GEO_LIVREUR = {activer, desactiver, etat: () => L};
if (location.hash === "#livreur") activer();
})();
