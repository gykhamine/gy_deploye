/* MODÈLE FANTÔME D'ONTOLOGIE — côté page (voir engine/fantome_ontologie.py pour le principe).

   Il ne parle JAMAIS à l'utilisateur. Deux rôles :
   1. OBSERVER  — à chaque pas réel (clic sur un nœud, recherche, voisin ouvert) : classer d'abord tous les nœuds avec le réseau
      courant (prédire), puis envoyer au serveur le nœud réellement choisi + des nœuds non choisis (apprendre). Le serveur entraîne
      le réseau et renvoie sa phase et ses poids. Tant que la phase est « observation », l'animation générique de la page continue.
   2. ASSISTER  — en phase « assistance » et après une inactivité, ce fichier anime l'écran à la place de l'utilisateur : caméra, mise au
      focus, trace des liens le long du plus court chemin, recherche d'un terme que l'utilisateur emploie souvent. Il alterne
      ce que l'utilisateur CHERCHE probablement, DEVRAIT voir (un nœud central jamais visité, dans sa zone d'intérêt) et POURRAIT explorer.
      Toute interaction réelle (souris, tactile, molette, clavier) l'arrête immédiatement.
   Chargé APRÈS le script de ontologie.html, dont il utilise les variables de portée globale (noeuds, adj, parIndex, focusId, mettreAuFocus…).
   Les textes passent par tj() : aucun texte gravé. */
(function () {
  "use strict";
  const URL_ETAT = "/api/ontologie/fantome/", URL_OBSERVER = "/api/ontologie/fantome/observer/";
  const SEUIL_INACTIVITE_MS = 4500, DUREE_TERME_MS = 90000, ENVOI_MS = 4000, MAX_FILE = 200, MAX_NEGATIFS = 12, NEGATIFS_DIFFICILES = 6;
  const CLE = c => "engine/static/engine/js/fantome_ontologie.js:" + c;
  const F = {
    pret: false, etat: null, poids: null, entrees: [], memo: { visites: {}, types: {}, transitions: {}, derniere: {}, termes: {} },
    agit: false, actif: false, plan: [], intention: "", tour: 0, prochainA: 0, precedent: null, terme: "", termeA: 0,
    dernierUtilisateur: Date.now(), dernierTermeA: 0, file: { episodes: [], pas: [], recherches: [] }, minuteurEnvoi: null, enVol: false, indicateur: null,
  };
  const INTENTIONS = ["cherche", "cherche", "devrait", "pourrait"];   // la plupart du temps ce que l'utilisateur chercherait

  // ── Réseau : même calcul que engine/reseau_neuronal.Reseau.predire (deux couches cachées tanh, sortie linéaire) ──
  function score(x) {
    const p = F.poids, H = p.b1.length;
    const a1 = new Array(H), a2 = new Array(H);
    for (let j = 0; j < H; j++) { let s = p.b1[j]; for (let i = 0; i < x.length; i++) s += x[i] * p.W1[i][j]; a1[j] = Math.tanh(s); }
    for (let j = 0; j < H; j++) { let s = p.b2[j]; for (let i = 0; i < H; i++) s += a1[i] * p.W2[i][j]; a2[j] = Math.tanh(s); }
    let o = p.b3[0]; for (let j = 0; j < H; j++) o += a2[j] * p.W3[j][0];
    return o;
  }

  // ── Caractéristiques (toutes dans [0, 1]) : les noms et leur ordre viennent du serveur (fantome_ontologie.FEATURES) ──
  const CAR = {
    voisin_direct: (n, c) => c.voisinsFocus.has(n.id) ? 1 : 0,
    voisin_du_precedent: (n, c) => c.voisinsPrec.has(n.id) ? 1 : 0,
    distance_deux: (n, c) => (c.deux.has(n.id) && !c.voisinsFocus.has(n.id)) ? 1 : 0,
    degre: (n, c) => Math.min(1, (adj[n.id] || []).length / c.maxDegre),
    meme_type: (n, c) => (c.noeudFocus && n.type === c.noeudFocus.type) ? 1 : 0,
    freq_noeud: (n, c) => Math.min(1, (F.memo.visites[n.id] || 0) / c.maxVisites),
    freq_type: (n, c) => Math.min(1, (F.memo.types[n.type] || 0) / c.maxType),
    transition: (n, c) => c.focus ? Math.min(1, (F.memo.transitions[c.focus + "\t" + n.id] || 0) / c.maxTransition) : 0,
    recence: (n, c) => { const t = F.memo.derniere[n.id]; return t ? Math.exp(-Math.max(0, c.maintenant - t) / 3600) : 0; },
    correspond_recherche: (n, c) => (c.terme && String(n.label).toLowerCase().includes(c.terme)) ? 1 : 0,
    jamais_vu: (n) => F.memo.visites[n.id] ? 0 : 1,
  };
  const voisinsDe = id => (adj[id] || []).filter(v => v in parIndex);
  function contexte(focus, precedent) {
    const vf = new Set(voisinsDe(focus || "")), vp = new Set(voisinsDe(precedent || "")), deux = new Set();
    for (const v of vf) for (const w of voisinsDe(v)) deux.add(w);
    let maxDegre = 1; for (const n of noeuds) maxDegre = Math.max(maxDegre, (adj[n.id] || []).length);
    let maxTransition = 1;
    if (focus) { const pre = focus + "\t"; for (const k in F.memo.transitions) if (k.startsWith(pre)) maxTransition = Math.max(maxTransition, F.memo.transitions[k]); }
    const t = F.terme && Date.now() - F.termeA < DUREE_TERME_MS ? F.terme : "";
    return {
      focus: focus || null, noeudFocus: focus ? noeuds[parIndex[focus]] : null, voisinsFocus: vf, voisinsPrec: vp, deux, maxDegre, maxTransition,
      maxVisites: Math.max(1, ...Object.values(F.memo.visites)), maxType: Math.max(1, ...Object.values(F.memo.types)),
      maintenant: Date.now() / 1000, terme: t,
    };
  }
  const vecteur = (n, c) => F.entrees.map(nom => CAR[nom] ? CAR[nom](n, c) : 0);

  // ── Mémoire de parcours (miroir local de celle du serveur, mise à jour tout de suite) ──
  function memoriser(memo, pas, termes) {
    const maintenant = Date.now() / 1000;
    for (const p of pas) {
      memo.visites[p.vers] = (memo.visites[p.vers] || 0) + 1; memo.derniere[p.vers] = maintenant;
      if (p.type) memo.types[p.type] = (memo.types[p.type] || 0) + 1;
      if (p.de && p.de !== p.vers) { const k = p.de + "\t" + p.vers; memo.transitions[k] = (memo.transitions[k] || 0) + 1; }
    }
    for (const t of termes) memo.termes[t] = (memo.termes[t] || 0) + 1;
  }

  // ── Observation d'un pas réel de l'utilisateur (appelé par mettreAuFocus) ──
  function pasUtilisateur(idVers, idDe) {
    if (F.agit || !F.pret || idVers === idDe) return;
    const vers = noeuds[parIndex[idVers]];
    if (!vers || noeuds.length < 3) return;
    const c = contexte(idDe, F.precedent), candidats = noeuds.filter(n => n.id !== idDe);
    const lignes = candidats.map(n => ({ n, v: vecteur(n, c) }));
    for (const l of lignes) l.s = score(l.v);
    const choisi = lignes.find(l => l.n.id === idVers);
    if (!choisi) return;
    const rang = 1 + lignes.reduce((k, l) => k + (l !== choisi && l.s > choisi.s ? 1 : 0), 0);
    const autres = lignes.filter(l => l !== choisi).sort((a, b) => b.s - a.s);
    const negatifs = autres.slice(0, NEGATIFS_DIFFICILES);   // les meilleurs nœuds que le réseau préférait à tort : ce sont eux qui lui apprennent le plus
    const reste = autres.slice(NEGATIFS_DIFFICILES);
    while (negatifs.length < MAX_NEGATIFS && reste.length) negatifs.push(reste.splice(Math.floor(Math.random() * reste.length), 1)[0]);
    const f = F.file;
    f.episodes.push({ positif: choisi.v, negatifs: negatifs.map(l => l.v), rang, candidats: candidats.length });
    const pas = { de: idDe || "", vers: idVers, type: vers.type || "" };
    f.pas.push(pas); memoriser(F.memo, [pas], []);
    F.precedent = idDe || null;
    planifierEnvoi();
  }
  function recherche(t) {
    if (F.agit || !F.pret) return;
    t = String(t || "").trim().toLowerCase();
    if (t.length < 2) return;
    F.terme = t; F.termeA = Date.now();
    const file = F.file.recherches, dernier = file[file.length - 1];
    if (dernier === t) return;
    if (dernier && t.startsWith(dernier) && Date.now() - F.dernierTermeA < 6000) {   // l'utilisateur est en train de taper : « cl », puis « clie », puis « clients » = une seule recherche
      file[file.length - 1] = t;
      if (F.memo.termes[dernier] > 1) F.memo.termes[dernier]--; else delete F.memo.termes[dernier];
      memoriser(F.memo, [], [t]);
    } else { file.push(t); memoriser(F.memo, [], [t]); }
    F.dernierTermeA = Date.now();
  }

  // ── Échanges avec le serveur ──
  function planifierEnvoi() {
    if (F.file.episodes.length > MAX_FILE) F.file.episodes.splice(0, F.file.episodes.length - MAX_FILE);
    if (!F.minuteurEnvoi) F.minuteurEnvoi = setTimeout(envoyer, ENVOI_MS);
  }
  function appliquerEtat(e) {
    if (!e || !e.poids) return;
    F.etat = e; F.poids = e.poids; F.entrees = e.entrees || [];
    F.memo = { visites: {}, types: {}, transitions: {}, derniere: {}, termes: {}, ...(e.memoire || {}) };
    memoriser(F.memo, F.file.pas, F.file.recherches);   // ce qui n'est pas encore parti reste dans la mémoire locale
    const bouton = document.getElementById("btn-explo-auto");
    if (bouton) {
      if (F.titreBouton === undefined) F.titreBouton = bouton.title || "";
      const texte = tj(CLE(e.phase === "assistance" ? "phase_assistance" : "phase_observation"), { v1: Math.round((e.comprehension || 0) * 100) });
      bouton.title = F.titreBouton ? F.titreBouton + "\n" + texte : texte;
    }
  }
  function csrf() { const m = document.cookie.match("(^|;)\\s*gy_csrftoken\\s*=\\s*([^;]+)"); return m ? m.pop() : ""; }
  async function envoyer(finDePage) {
    F.minuteurEnvoi = null;
    const f = F.file;
    if (F.enVol || (!f.episodes.length && !f.pas.length && !f.recherches.length)) return;
    F.enVol = true;
    const lot = { episodes: f.episodes.splice(0), pas: f.pas.splice(0), recherches: f.recherches.splice(0) };
    try {
      const rep = await fetch(URL_OBSERVER, { method: "POST", keepalive: !!finDePage, headers: { "Content-Type": "application/json", "X-CSRFToken": csrf(), "X-Silencieux": "1" }, body: JSON.stringify(lot) });
      if (!rep.ok) throw new Error(String(rep.status));
      appliquerEtat(await rep.json());
    } catch (err) {   // réseau ou serveur indisponible : on remet en file, sans rien dire à l'utilisateur, et on réessaie plus tard
      f.episodes.unshift(...lot.episodes); f.pas.unshift(...lot.pas); f.recherches.unshift(...lot.recherches);
      if (f.episodes.length > MAX_FILE) f.episodes.splice(0, f.episodes.length - MAX_FILE);
      F.minuteurEnvoi = setTimeout(envoyer, ENVOI_MS * 5);
    } finally { F.enVol = false; }
  }
  window.addEventListener("pagehide", () => envoyer(true));

  // ── Assistance : l'écran s'anime seul, mené par le réseau ──
  const enAssistance = () => !!(F.pret && F.etat && F.etat.phase === "assistance" && typeof explorationAutoPermise !== "undefined" && explorationAutoPermise);

  function arreter() {
    F.actif = false; F.plan = [];
    if (F.indicateur) F.indicateur.style.display = "none";
  }
  function interaction() { F.dernierUtilisateur = Date.now(); if (F.actif) arreter(); }
  for (const evt of ["pointerdown", "wheel", "keydown", "touchstart"]) document.addEventListener(evt, interaction, { capture: true, passive: true });

  function indiquer(intention, noeud) {
    if (!F.indicateur) {
      F.indicateur = document.createElement("div");
      F.indicateur.style.cssText = "position:absolute;left:12px;bottom:44px;z-index:5;pointer-events:none;max-width:60%;padding:4px 10px;border-radius:6px;font-size:12px;"
        + "background:color-mix(in srgb,var(--fond-info,#0f172a) 85%,transparent);color:var(--texte-info,#7dd3fc);border:1px solid var(--bordure-info,#1e3a5f);";
      (document.getElementById("scene") || document.body).appendChild(F.indicateur);
    }
    F.indicateur.textContent = tj(CLE("pas_assistance"), { v1: tj(CLE("intention_" + intention)), v2: String(noeud.label).slice(0, 40) });
    F.indicateur.style.display = "block";
  }

  // Les trois intentions, d'après le classement du réseau depuis le nœud actuellement au focus.
  function choisirCible(intention) {
    const c = contexte(typeof focusId === "undefined" ? null : focusId, F.precedent);
    const lignes = noeuds.filter(n => n.id !== c.focus).map(n => ({ n, v: vecteur(n, c) }));
    if (!lignes.length) return null;
    for (const l of lignes) l.s = score(l.v);
    lignes.sort((a, b) => b.s - a.s);
    if (intention === "devrait") {   // un nœud central que l'utilisateur n'a jamais vu, là où le réseau pense qu'il regarde
      const inedits = lignes.filter(l => !F.memo.visites[l.n.id]).sort((a, b) => b.s * (0.5 + CAR.degre(b.n, c)) - a.s * (0.5 + CAR.degre(a.n, c)));
      if (inedits.length) return inedits[0].n;
    }
    if (intention === "pourrait") {   // tirage pondéré (softmax) parmi les nœuds proches : on explore sans toujours choisir le premier
      const proches = lignes.filter(l => c.voisinsFocus.has(l.n.id) || c.deux.has(l.n.id)).slice(0, 12), pool = proches.length ? proches : lignes.slice(0, 12);
      const m = pool[0].s, poids = pool.map(l => Math.exp((l.s - m) / 0.15)), total = poids.reduce((a, b) => a + b, 0);
      let r = Math.random() * total;
      for (let i = 0; i < pool.length; i++) { r -= poids[i]; if (r <= 0) return pool[i].n; }
      return pool[0].n;
    }
    return lignes[0].n;   // « cherche » : ce que l'utilisateur ferait le plus probablement
  }

  function agir(fn) { F.agit = true; try { fn(); } finally { F.agit = false; } }

  // Recherche faite à la place de l'utilisateur : un terme qu'il emploie souvent et qui mène à un nœud qu'il n'a pas encore vu.
  function rechercheFantome() {
    const termes = Object.entries(F.memo.termes || {}).sort((a, b) => b[1] - a[1]).slice(0, 5).map(e => e[0]);
    const courant = typeof focusId === "undefined" ? null : focusId;
    for (const t of termes) {
      const cible = noeuds.find(n => n.id !== courant && !F.memo.visites[n.id] && String(n.label).toLowerCase().includes(t));
      if (!cible) continue;
      agir(() => { champRecherche.value = t; rechercheTexte = t; chercherEtCentrer(false); });
      indiquer("cherche", cible);
      return true;
    }
    return false;
  }

  function construirePlan() {
    const intention = INTENTIONS[F.tour++ % INTENTIONS.length];
    if (intention === "cherche" && Math.random() < 0.35 && rechercheFantome()) { F.plan = []; F.intention = intention; F.prochainA = performance.now() + 2600; return false; }
    const cible = choisirCible(intention);
    if (!cible) return false;
    const depart = typeof focusId === "undefined" ? null : focusId;
    let chemin = depart ? cheminCourt(depart, cible.id) : [];
    chemin = chemin.length > 1 && chemin.length <= 7 ? chemin.slice(1) : [cible.id];   // plus court chemin si raisonnable, sinon on y va directement
    F.plan = chemin.map((id, i) => ({ id, premier: i === 0 })); F.intention = intention;
    return true;
  }

  function pasAssistance() {
    if (!F.plan.length && !construirePlan()) return;
    const etape = F.plan.shift();
    if (!etape) return;
    const n = noeuds[parIndex[etape.id]];
    if (!n) { F.plan = []; return; }
    const avant = typeof dernierInteraction === "undefined" ? null : dernierInteraction, ancienFocus = typeof focusId === "undefined" ? null : focusId;
    agir(() => mettreAuFocus(etape.id, false, etape.premier));   // la trace des liens s'accumule d'un pas à l'autre, comme à la main
    if (avant !== null) dernierInteraction = avant;
    F.precedent = ancienFocus;   // le nœud quitté devient le « précédent » du nouveau focus, comme pour un pas de l'utilisateur
    indiquer(F.intention, n);
    F.prochainA = performance.now() + (F.plan.length ? 1900 : 3200) + Math.random() * 900;   // une pause plus longue en arrivant : le temps de lire
  }

  function occupe() {   // l'utilisateur écrit quelque part, ou une fenêtre est ouverte : l'animation ne s'en mêle pas
    const a = document.activeElement;
    return !!(a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) || !!document.querySelector(".modal-fond[style*='display: flex'], .modal-fond[style*='display:flex']");
  }
  function tic() {
    if (!enAssistance()) { if (F.actif) arreter(); return; }
    if (document.hidden || occupe()) return;
    if (!F.actif) {
      if (Date.now() - F.dernierUtilisateur < SEUIL_INACTIVITE_MS) return;
      F.actif = true; F.plan = []; F.prochainA = 0;
    }
    if (performance.now() >= F.prochainA) pasAssistance();
  }

  async function demarrer() {
    try {
      const rep = await fetch(URL_ETAT, { credentials: "same-origin", headers: { "X-Silencieux": "1" } });
      if (!rep.ok) return;
      appliquerEtat(await rep.json());
      F.pret = !!F.poids;
    } catch (err) { /* sans serveur, la page garde son animation générique */ }
    setInterval(tic, 600);
  }
  window.FantomeOnto = { agit: () => F.agit, pasUtilisateur, recherche, enAssistance, envoyer, score, vecteur, contexte };
  demarrer();
})();
