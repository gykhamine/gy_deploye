// Bandeau permanent + sélecteur de saison (voir middleware.SaisonMiddleware, views_saisons). Gris = saison en cours, ambre = restaurée en lecture seule, rouge = écriture ouverte.
(async () => {
  const SRC = (document.currentScript && document.currentScript.src) || "";
  // Dialogues de la plateforme (dialogues.js), chargés à la demande si la page ne les a pas déjà : jamais confirm() du navigateur.
  const dialogues = () => window.GY && window.GY.confirmer ? Promise.resolve(window.GY) : new Promise((ok, ko) => {
    const sc = document.createElement("script"); sc.src = SRC.replace(/saisons\.js(\?.*)?$/, "dialogues.js$1"); sc.onload = () => ok(window.GY); sc.onerror = ko; document.head.appendChild(sc);
  });
  const csrf = () => document.cookie.match(/gy_csrftoken=([^;]+)/)?.[1] || "";
  const appel = async (corps) => (await fetch("/api/saisons/", corps ? {method: "POST", headers: {"X-CSRFToken": csrf(), "Content-Type": "application/json"}, body: JSON.stringify(corps)} : {})).json();
  const {saisons} = await appel(); const s = saisons.find(x => x.actif);
  if (!s || saisons.length < 2) return;
  const bar = document.createElement("div"), couleur = !s.restauree ? "#475569" : s.ecriture ? "#b91c1c" : "#b45309";
  bar.style.cssText = `position:sticky;top:0;z-index:99999;display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;padding:.35rem .8rem;color:#fff;font:600 13px system-ui;background:${couleur}`;
  const sel = document.createElement("select"); sel.style.cssText = "max-width:60vw;border-radius:4px;padding:2px 4px";
  saisons.forEach(x => sel.add(new Option((x.restauree ? tj("engine/static/engine/js/saisons.js:15:59") : tj("engine/static/engine/js/saisons.js:15:83")) + x.nom, x.entreprise_id, false, x.actif)));
  sel.onchange = async () => { await appel({entreprise_id: +sel.value}); location.reload(); };
  bar.append(sel, s.restauree ? (s.ecriture ? tj("engine/static/engine/js/saisons.js:17:48") : tj("engine/static/engine/js/saisons.js:17:129")) : tj("engine/static/engine/js/saisons.js:17:177"));
  if (s.restauree && s.admin) {
    const b = document.createElement("button"); b.textContent = s.ecriture ? tj("engine/static/engine/js/saisons.js:19:79") : tj("engine/static/engine/js/saisons.js:19:109");
    b.onclick = async () => { if (s.ecriture || await dialogues().then(G => G.confirmer(tj("engine/static/engine/js/saisons.js:20:90")))) { await appel({ecriture: !s.ecriture}); location.reload(); } };
    bar.append(b);
  }
  document.body.prepend(bar);
})();
