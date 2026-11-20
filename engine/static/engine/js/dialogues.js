// ── Boîtes de dialogue de la plateforme : remplacent confirm() / alert() / prompt() du navigateur ───────────────────────
// Même design et mêmes couleurs que le reste de l'interface (variables CSS du thème de l'administrateur : --panel, --border,
// --text, --accent). Asynchrones : `await GY.confirmer(msg)` → true/false ; `await GY.demander(msg, defaut)` → texte ou null ;
// `GY.alerter(msg)` → information (les alertes s'enchaînent l'une après l'autre). Entrée valide, Échap annule.
// Libellés traduits (clés tj) ; le français sert de repli si la table de traduction n'est pas encore chargée.
(function(){
  if (window.GY && window.GY.confirmer) return;
  const T = (cle, repli) => { try { const v = typeof tj === "function" ? tj("engine/static/engine/js/dialogues.js:" + cle) : ""; return v && v !== "engine/static/engine/js/dialogues.js:" + cle ? v : repli; } catch (e) { return repli; } };
  const CSS = `
  .gy-dlg-voile{position:fixed;inset:0;z-index:100000;display:flex;align-items:center;justify-content:center;padding:20px;background:color-mix(in srgb,var(--voile,#000) 62%,transparent);backdrop-filter:blur(2px);animation:gy-dlg-fondu .12s ease-out}
  .gy-dlg{width:min(440px,100%);max-height:90vh;overflow:auto;background:var(--panel,#0a0a0a);color:var(--text,#e6edf3);border:1px solid var(--border,#2a2a2a);border-radius:14px;padding:22px 22px 18px;box-shadow:0 18px 60px color-mix(in srgb,var(--voile,#000) 55%,transparent);font:inherit;animation:gy-dlg-monte .14s ease-out}
  .gy-dlg-titre{font-size:15px;font-weight:700;margin-bottom:10px;color:var(--text,#e6edf3)}
  .gy-dlg-msg{font-size:14px;line-height:1.55;white-space:pre-line;overflow-wrap:anywhere;color:var(--text,#e6edf3)}
  .gy-dlg-champ{width:100%;margin-top:14px;background:var(--bg,#000);color:var(--text,#e6edf3);border:1px solid var(--border,#2a2a2a);border-radius:8px;padding:10px 12px;font:inherit;font-size:14px}
  .gy-dlg-champ:focus{outline:none;border-color:var(--accent,#4aa3df)}
  .gy-dlg-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:20px;flex-wrap:wrap}
  .gy-dlg-actions button{font:inherit;font-size:14px;font-weight:600;padding:9px 18px;border-radius:9px;cursor:pointer;border:1px solid var(--border,#2a2a2a);background:transparent;color:var(--text,#e6edf3)}
  .gy-dlg-actions button:hover{border-color:var(--accent,#4aa3df)}
  .gy-dlg-actions button.gy-dlg-ok{background:var(--accent,#4aa3df);border-color:var(--accent,#4aa3df);color:var(--bg,#000)}
  .gy-dlg-actions button.gy-dlg-danger{background:var(--danger,#ef4444);border-color:var(--danger,#ef4444);color:var(--texte-sombre,#fff)}
  .gy-dlg-actions button:focus-visible{outline:2px solid var(--accent,#4aa3df);outline-offset:2px}
  @keyframes gy-dlg-fondu{from{opacity:0}to{opacity:1}}
  @keyframes gy-dlg-monte{from{transform:translateY(8px) scale(.98);opacity:0}to{transform:none;opacity:1}}
  @media (prefers-reduced-motion:reduce){.gy-dlg-voile,.gy-dlg{animation:none}}`;
  let fileAttente = Promise.resolve();
  function style(){
    if (document.getElementById("gy-dlg-css")) return;
    const s = document.createElement("style"); s.id = "gy-dlg-css"; s.textContent = CSS; (document.head || document.documentElement).appendChild(s);
  }
  function ouvrir(o){   // o : {message, titre, champ, defaut, oui, non, danger} → Promise<{ok, valeur}>
    return new Promise(fin => {
      style();
      const precedent = document.activeElement;
      const voile = document.createElement("div"); voile.className = "gy-dlg-voile";
      const boite = document.createElement("div"); boite.className = "gy-dlg"; boite.setAttribute("role", o.non ? "alertdialog" : "dialog"); boite.setAttribute("aria-modal", "true");
      if (o.titre){ const h = document.createElement("div"); h.className = "gy-dlg-titre"; h.textContent = o.titre; boite.appendChild(h); }
      const msg = document.createElement("div"); msg.className = "gy-dlg-msg"; msg.textContent = o.message == null ? "" : String(o.message); boite.appendChild(msg);
      let champ = null;
      if (o.champ){ champ = document.createElement("input"); champ.type = o.masque ? "password" : "text"; if (o.masque) champ.autocomplete = "off"; champ.className = "gy-dlg-champ"; champ.value = o.defaut == null ? "" : String(o.defaut); boite.appendChild(champ); }
      const barre = document.createElement("div"); barre.className = "gy-dlg-actions";
      const bNon = o.non ? document.createElement("button") : null;
      const bOui = document.createElement("button"); bOui.type = "button"; bOui.textContent = o.oui; bOui.className = o.danger ? "gy-dlg-danger" : "gy-dlg-ok";
      if (bNon){ bNon.type = "button"; bNon.textContent = o.non; barre.appendChild(bNon); }
      barre.appendChild(bOui); boite.appendChild(barre); voile.appendChild(boite);
      const clore = (ok) => {
        document.removeEventListener("keydown", touche, true); voile.remove();
        try { if (precedent && precedent.focus) precedent.focus(); } catch (e) {}
        fin({ok, valeur: champ ? champ.value : null});
      };
      const touche = ev => {
        if (ev.key === "Escape"){ ev.preventDefault(); ev.stopPropagation(); clore(false); }
        else if (ev.key === "Enter" && !(ev.target && ev.target.tagName === "BUTTON" && ev.target !== bOui)){ ev.preventDefault(); ev.stopPropagation(); clore(true); }
        else if (ev.key === "Tab"){   // le focus reste dans la boîte
          const f = [...boite.querySelectorAll("input,button")], i = f.indexOf(document.activeElement);
          ev.preventDefault(); f[(i + (ev.shiftKey ? f.length - 1 : 1)) % f.length].focus();
        }
      };
      document.addEventListener("keydown", touche, true);
      bOui.onclick = () => clore(true);
      if (bNon) bNon.onclick = () => clore(false);
      voile.addEventListener("mousedown", ev => { if (ev.target === voile && bNon) clore(false); });
      document.body.appendChild(voile);
      (champ || bOui).focus(); if (champ) champ.select();
    });
  }
  const enfiler = o => { const p = fileAttente.then(() => ouvrir(o)); fileAttente = p.catch(() => {}); return p; };
  // ── Garde de navigation : « des modifications ne sont pas enregistrées » avec la boîte de la plateforme, jamais celle du navigateur ──
  // GY.garderNavigation(() => modifie, {message, ignorer(url)}) : tant que la fonction renvoie vrai, un clic sur un lien, une navigation par
  // script (location.href = …) ou un retour arrière demandent confirmation avec GY.confirmer ; `ignorer(url)` laisse passer certaines adresses
  // (téléchargements). Le bouton Retour du navigateur est capté par une entrée d'historique sentinelle. Seule la FERMETURE de l'onglet / le
  // rechargement ne peut pas passer par une boîte à nous (le navigateur l'interdit) : il garde alors son message natif, uniquement dans ce cas.
  function garderNavigation(estModifie, opts){
    opts = opts || {};
    let autorise = false, enAttente = false;
    const message = () => typeof opts.message === "function" ? opts.message() : (opts.message || T("quitter", tj("engine/static/engine/js/dialogues.js:75:112")));
    // adresse à laisser passer (téléchargement) : le navigateur déclencherait quand même son message natif, on lève donc la garde un instant
    const liberer = (ms) => { autorise = true; setTimeout(() => { autorise = false; }, ms || 1000); };
    const ignore = url => { try { if (opts.ignorer && opts.ignorer(url)){ liberer(); return true; } } catch (e) { /* garde active */ } return false; };
    const demander = suite => {
      if (enAttente) return;
      enAttente = true;
      enfiler({message: message(), oui: T("quitter_oui", tj("engine/static/engine/js/dialogues.js:82:59")), non: T("quitter_non", "Rester"), danger: true}).then(r => {
        enAttente = false;
        if (r.ok){ autorise = true; suite(); }
      });
    };
    document.addEventListener("click", e => {
      if (autorise || !estModifie() || e.defaultPrevented) return;
      const a = e.target && e.target.closest && e.target.closest("a[href]");
      if (!a || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || a.hasAttribute("download") || (a.target && a.target !== "_self")) return;
      if ((a.getAttribute("href") || "").charAt(0) === "#") return;
      let u; try { u = new URL(a.href, location.href); } catch (err) { return; }
      if (!/^https?:$/.test(u.protocol) || ignore(u.href) || (u.pathname === location.pathname && u.search === location.search && u.hash)) return;
      e.preventDefault();
      demander(() => location.assign(u.href));
    }, true);
    if (window.navigation && typeof window.navigation.addEventListener === "function"){   // navigation par script, retour arrière : l'API Navigation peut les annuler
      window.navigation.addEventListener("navigate", e => {
        if (autorise || !estModifie() || !e.cancelable || e.hashChange || e.downloadRequest !== null || e.formData || ignore(e.destination.url)) return;
        const d = e.destination;
        if (d.sameDocument) return;
        e.preventDefault();
        const type = e.navigationType;
        demander(() => { type === "traverse" ? window.navigation.traverseTo(d.key) : type === "reload" ? location.reload() : type === "replace" ? location.replace(d.url) : location.assign(d.url); });
      });
    }
    // Bouton Retour du navigateur : le navigateur ne laisse pas l'annuler, alors on intercale une entrée d'historique « sentinelle » dès la première saisie.
    // Un Retour ne quitte donc pas la page : il retire seulement la sentinelle (événement popstate, sans message natif) et c'est notre boîte qui s'affiche.
    let sentinelle = false;
    const poserSentinelle = () => { if (sentinelle || autorise || !estModifie()) return; try { history.pushState({gyGarde: true}, "", location.href); sentinelle = true; } catch (e) { /* sans historique : message natif */ } };
    ["input", "change", "keydown", "pointerdown"].forEach(t => document.addEventListener(t, () => setTimeout(poserSentinelle, 0), true));
    window.addEventListener("popstate", () => {
      if (!sentinelle || (history.state && history.state.gyGarde)) return;
      sentinelle = false;
      if (autorise) return;
      if (!estModifie()){ history.back(); return; }
      poserSentinelle();   // on reste sur la page le temps de la réponse
      if (enAttente) return;
      demander(() => { sentinelle = false; history.go(-2); });   // quitter : on saute la sentinelle et la page courante
    });
    window.addEventListener("beforeunload", ev => { if (!autorise && estModifie()){ ev.preventDefault(); ev.returnValue = ""; } });
    window.addEventListener("pageshow", () => { autorise = false; });
    return {liberer};
  }
  window.GY = Object.assign(window.GY || {}, {
    garderNavigation,
    confirmer: (message, opts) => enfiler(Object.assign({message, oui: T("oui", tj("engine/static/engine/js/dialogues.js:127:82")), non: T("non", tj("engine/static/engine/js/dialogues.js:127:110"))}, opts || {})).then(r => r.ok),
    alerter: (message, opts) => enfiler(Object.assign({message, oui: T("ok", "OK")}, opts || {})).then(() => undefined),
    demander: (message, defaut, opts) => enfiler(Object.assign({message, champ: true, defaut, oui: T("valider", tj("engine/static/engine/js/dialogues.js:129:114")), non: T("non", tj("engine/static/engine/js/dialogues.js:129:140"))}, opts || {})).then(r => r.ok ? r.valeur : null),
  });
})();
