// Test d'utilisabilité statistique (engine/usabilite.py) — injecté dans chaque page d'un utilisateur connecté.
// Mesure : temps visible par page, actions (réussite, durée), erreurs, clics répétés, tâches chronométrées.
// Ne capture JAMAIS le contenu saisi ni les valeurs des champs : seulement chemins, types et durées.
(() => {
  if (window.gyUsabilite) return;
  const CSRF = (document.cookie.match(/gy_csrftoken=([^;]+)/) || [])[1] || "";
  const normaliser = u => {
    try { return new URL(u, location.href).pathname.replace(/\/\d+(?=\/|$)/g, "/:id").slice(0, 200); }
    catch (e) { return ""; }
  };
  const page = normaliser(location.href);
  let session = "";
  try { session = sessionStorage.getItem("gy_session") || ""; } catch (e) {}
  if (!session) {
    session = Math.random().toString(36).slice(2) + Date.now().toString(36);
    try { sessionStorage.setItem("gy_session", session); } catch (e) {}
  }
  const file = [];
  const ajouter = e => { if (file.length < 300) file.push(Object.assign({ page }, e)); };
  const origFetch = window.fetch.bind(window);
  function envoyer(final) {
    if (!file.length) return;
    const lot = file.splice(0, 100);
    origFetch("/api/usabilite/evenements/", {
      method: "POST", keepalive: !!final, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
      body: JSON.stringify({ session, evenements: lot }),
    }).catch(() => {});
  }
  setInterval(() => envoyer(false), 20000);

  // Temps VISIBLE passé sur la page (onglet en arrière-plan non compté)
  let visible = document.visibilityState === "visible" ? performance.now() : null, cumul = 0, pageEnvoyee = false;
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") visible = performance.now();
    else { if (visible !== null) cumul += performance.now() - visible; visible = null; envoyer(true); }
  });
  addEventListener("pagehide", () => {
    if (pageEnvoyee) return;
    pageEnvoyee = true;
    if (visible !== null) cumul += performance.now() - visible;
    ajouter({ type: "page", duree_ms: Math.round(cumul) });
    envoyer(true);
  });

  addEventListener("error", e => { if (e.message) ajouter({ type: "erreur_js", cible: String(e.message).slice(0, 200) }); });

  // Clics répétés : 3 clics en moins de 0,8 s au même endroit = signe de frustration (élément qui ne répond pas)
  let clics = [];
  const decrire = el => {
    if (!el || !el.tagName) return "";
    const base = el.tagName.toLowerCase() + (el.id ? "#" + el.id : "");
    const txt = /^(button|a)$/i.test(el.tagName) ? " « " + (el.textContent || "").trim().slice(0, 30) + " »" : "";
    return (base + txt).slice(0, 200);
  };
  addEventListener("click", e => {
    const t = performance.now();
    clics = clics.filter(c => t - c.t < 800 && Math.hypot(c.x - e.clientX, c.y - e.clientY) < 30);
    clics.push({ t, x: e.clientX, y: e.clientY });
    if (clics.length >= 3) { ajouter({ type: "clic_rageur", cible: decrire(e.target) }); clics = []; }
  }, true);

  // Ouvertures d'onglets PAR TABLE : « table:<id>:<onglet> » (GET /api/tables/<id>/<onglet>/), au plus une par minute et par couple.
  // Aucun contenu : seulement l'identifiant de la table et le nom de l'onglet (usage réel que le modèle fantôme apprend à lire).
  const vues = new Map();
  function ouverture(url) {
    let m;
    try { m = new URL(url, location.href).pathname.match(/^\/api\/tables\/(\d+)\/([a-z0-9-]+)\/?$/); } catch (e) { return; }
    if (!m) return;
    const cle = "table:" + m[1] + ":" + m[2], t = Date.now();
    if (t - (vues.get(cle) || 0) < 60000) return;
    vues.set(cle, t);
    ajouter({ type: "ouverture", cible: cle, succes: true });
  }

  // Actions : toute requête qui modifie (POST/PUT/PATCH/DELETE) = une action réussie ou non, chronométrée
  window.fetch = async (entree, init) => {
    const url = typeof entree === "string" ? entree : (entree && entree.url) || "";
    const methode = ((init && init.method) || (entree && entree.method) || "GET").toUpperCase();
    if (url.includes("/api/usabilite/")) return origFetch(entree, init);
    const t0 = performance.now(), cible = methode + " " + normaliser(url);
    try {
      const r = await origFetch(entree, init);
      // 423 sur le verrou d'édition = « quelqu'un d'autre modifie ce brouillon » : réponse normale du renouvellement (toutes les 20 s), pas un échec
      const verrouTenu = r.status === 423 && methode === "POST" && cible.endsWith("/verrou/");
      if (methode !== "GET") ajouter({ type: "action", cible, succes: r.ok || verrouTenu, duree_ms: Math.round(performance.now() - t0) });
      else if (r.ok) ouverture(url);
      else if (r.status >= 500) ajouter({ type: "erreur_api", cible, succes: false });
      return r;
    } catch (err) {
      ajouter({ type: methode !== "GET" ? "action" : "erreur_api", cible, succes: false, duree_ms: Math.round(performance.now() - t0) });
      throw err;
    }
  };

  // API pour chronométrer une tâche précise : const t = gyUsabilite.tache("Créer une facture"); … t.fin(true)
  window.gyUsabilite = {
    tache(nom) {
      const t0 = performance.now();
      return { fin: ok => ajouter({ type: "tache", cible: String(nom).slice(0, 200), succes: !!ok, duree_ms: Math.round(performance.now() - t0) }) };
    },
  };

  // Questionnaire SUS (10 affirmations, 1 = pas du tout d'accord … 5 = tout à fait d'accord)
  let plusTard = 0;
  try { plusTard = +localStorage.getItem("gy_sus_plus_tard") || 0; } catch (e) {}
  if (Date.now() < plusTard) return;
  setTimeout(async () => {
    const r = await origFetch("/api/usabilite/sus/", { credentials: "same-origin" }).catch(() => null);
    const d = r && r.ok ? await r.json().catch(() => ({})) : {};
    if (d.demander && d.questions && d.questions.length === 10) afficherSUS(d.questions);
  }, 30000);

  function el(tag, attrs, ...enfants) {
    const n = document.createElement(tag);
    Object.assign(n, attrs || {});
    enfants.forEach(c => n.append(c));
    return n;
  }
  function afficherSUS(questions) {
    const fond = el("div");
    fond.style.cssText = "position:fixed;inset:0;background:color-mix(in srgb,var(--voile,#000000) 60%,transparent);z-index:99999;display:flex;align-items:center;justify-content:center;padding:16px";
    const boite = el("div");
    boite.style.cssText = "background:var(--bg-card,#1b1d21);color:var(--text,#e8e8e8);border:1px solid var(--border,#333);border-radius:10px;max-width:640px;width:100%;max-height:90vh;overflow:auto;padding:18px;font-size:14px";
    boite.append(el("h3", { textContent: tj("engine/static/engine/js/usabilite.js:123:43") }),
      el("p", { textContent: tj("engine/static/engine/js/usabilite.js:124:31") }));
    const choix = [];
    questions.forEach((q, i) => {
      const ligne = el("div");
      ligne.style.cssText = "margin:10px 0;padding-bottom:8px;border-bottom:1px solid var(--border,#333)";
      const notes = el("div");
      notes.style.cssText = "display:flex;gap:6px;margin-top:6px;align-items:center";
      notes.append(el("span", { textContent: tj("engine/static/engine/js/usabilite.js:131:47") }));
      for (let v = 1; v <= 5; v++) {
        const b = el("button", { type: "button", textContent: String(v) });
        b.style.cssText = "min-width:38px;padding:6px;border-radius:6px;border:1px solid var(--border,#444);background:transparent;color:inherit;cursor:pointer";
        b.onclick = () => { choix[i] = v; [...notes.querySelectorAll("button")].forEach(x => x.style.background = "transparent"); b.style.background = "var(--accent,#3b82f6)"; };
        notes.append(b);
      }
      notes.append(el("span", { textContent: tj("engine/static/engine/js/usabilite.js:138:47") }));
      ligne.append(el("div", { textContent: (i + 1) + ". " + q }), notes);
      boite.append(ligne);
    });
    const commentaire = el("textarea", { placeholder: tj("engine/static/engine/js/usabilite.js:142:56"), rows: 3 });
    commentaire.style.cssText = "width:100%;margin-top:8px";
    const erreur = el("div");
    erreur.style.color = "var(--texte-erreur,#f87171)";
    const envoyerBtn = el("button", { type: "button", textContent: tj("engine/static/engine/js/usabilite.js:146:69") });
    const tardBtn = el("button", { type: "button", textContent: tj("engine/static/engine/js/usabilite.js:147:66") });
    const actions = el("div", {}, tardBtn, envoyerBtn);
    actions.style.cssText = "display:flex;gap:8px;justify-content:flex-end;margin-top:10px";
    tardBtn.onclick = () => { try { localStorage.setItem("gy_sus_plus_tard", Date.now() + 7 * 86400000); } catch (e) {} fond.remove(); };
    envoyerBtn.onclick = async () => {
      if (choix.filter(Boolean).length !== 10) { erreur.textContent = tj("engine/static/engine/js/usabilite.js:152:72"); return; }
      const r = await origFetch("/api/usabilite/sus/", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
        body: JSON.stringify({ reponses: choix, commentaire: commentaire.value }),
      }).catch(() => null);
      if (r && r.ok) fond.remove(); else erreur.textContent = tj("engine/static/engine/js/usabilite.js:158:64");
    };
    boite.append(commentaire, erreur, actions);
    fond.append(boite);
    document.body.append(fond);
  }
})();
