/* MODULE MANQUANT — injecté dans toute page HTML par engine.modules_manquants.ModuleManquantMiddleware.
   Toute réponse 424 + en-tête X-Module-Manquant (fetch, XMLHttpRequest, ou page de navigation) ouvre un pop-up : la fonctionnalité est bloquée,
   contactez l'administrateur (WhatsApp / e-mail si configurés). Textes déjà traduits par le serveur ; tout est posé en textContent. */
(function () {
  "use strict";
  if (window.__gyModuleManquant) return;
  window.__gyModuleManquant = true;
  function afficher(d) {
    if (!d || document.getElementById("gy-mm-dialogue")) return;
    const T = d.textes || {}, dlg = document.createElement("dialog");
    dlg.id = "gy-mm-dialogue";
    dlg.style.cssText = "max-width:min(32rem,92vw);padding:1.25rem 1.5rem;border:1px solid #8886;border-radius:12px;font:15px/1.5 system-ui,sans-serif";
    dlg.setAttribute("aria-labelledby", "gy-mm-titre");
    const h = document.createElement("h2"); h.id = "gy-mm-titre"; h.textContent = T.titre || ""; h.style.cssText = "margin:0 0 .5rem;font-size:1.15rem";
    const p = document.createElement("p"); p.textContent = d.erreur || ""; p.style.margin = "0 0 1rem";
    const rang = document.createElement("div"); rang.style.cssText = "display:flex;flex-wrap:wrap;gap:.5rem;align-items:center";
    const lien = (url, texte) => { const a = document.createElement("a"); a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer"; a.textContent = texte;
      a.style.cssText = "padding:.45rem .9rem;border-radius:8px;border:1px solid #8888;text-decoration:none;color:inherit;font-weight:600"; return a; };
    if (d.whatsapp) rang.append(lien(d.whatsapp, T.whatsapp || "WhatsApp"));
    if (d.mail) rang.append(lien(d.mail, T.mail || "E-mail"));
    if (!d.whatsapp && !d.mail) { const s = document.createElement("small"); s.textContent = T.absent || ""; rang.append(s); }
    const f = document.createElement("button"); f.type = "button"; f.textContent = T.fermer || "×"; f.style.cssText = "margin-left:auto;padding:.45rem .9rem;border-radius:8px;cursor:pointer";
    f.addEventListener("click", () => dlg.close());
    rang.append(f);
    dlg.append(h, p, rang);
    dlg.addEventListener("close", () => dlg.remove());
    document.body.append(dlg);
    dlg.showModal();
  }
  const pris = r => r && r.status === 424 && r.headers.get("X-Module-Manquant");
  const fetchOrigine = window.fetch;
  window.fetch = function () {
    return fetchOrigine.apply(this, arguments).then(r => { if (pris(r)) r.clone().json().then(afficher).catch(() => {}); return r; });
  };
  const envoyer = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function () {
    this.addEventListener("load", () => {
      if (this.status !== 424 || !this.getResponseHeader("X-Module-Manquant")) return;
      try { afficher(JSON.parse(this.responseText)); } catch (_) { /* réponse non textuelle */ }
    });
    return envoyer.apply(this, arguments);
  };
  const page = () => { const n = document.getElementById("gy-mm"); if (n) { try { afficher(JSON.parse(n.textContent)); } catch (_) { /* page sans données */ } } };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", page); else page();
})();
