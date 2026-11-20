// Notifications du navigateur (API Notification) — point d'entrée UNIQUE de la plateforme.
//   GYNotif.afficher({titre, message, lien, tag, id, type, seulement_masquee}) → true si une notification du navigateur a été affichée
//   GYNotif.recevoir(donnees)   : message du WebSocket /ws/notifications/ ({type:"notification", id, titre, message, lien})
//   GYNotif.demander()          : demande la permission (à appeler depuis un clic ; sinon le premier clic de la page s'en charge)
//   GYNotif.activer() / desactiver() / etat() / lier(bouton) : réglage par l'utilisateur (« Activer les notifications du navigateur »)
// Sans permission (ou API absente) afficher() renvoie false : l'appelant garde alors son message à l'écran (toast, bandeau).
(function () {
  if (window.GYNotif) return;
  const PREF = "gy-notif-navigateur";          // "off" : désactivées par l'utilisateur ; "refus" : il a fermé/refusé la demande
  const PREFIXE = "engine/static/engine/js/notifications_navigateur.js:";
  const REPLI = {
    activer: tj("engine/static/engine/js/notifications_navigateur.js:12:15"),
    actives: tj("engine/static/engine/js/notifications_navigateur.js:13:15"),
    coupees: tj("engine/static/engine/js/notifications_navigateur.js:14:15"),
    bloquees: tj("engine/static/engine/js/notifications_navigateur.js:15:16"),
    indisponibles: tj("engine/static/engine/js/notifications_navigateur.js:16:21"),
    titre: "Agentgy",
  };
  const T = cle => { try { const v = typeof tj === "function" ? tj(PREFIXE + cle) : ""; return v && v !== PREFIXE + cle ? v : REPLI[cle]; } catch (e) { return REPLI[cle]; } };
  const pref = () => { try { return localStorage.getItem(PREF) || ""; } catch (e) { return ""; } };
  const poser = v => { try { v ? localStorage.setItem(PREF, v) : localStorage.removeItem(PREF); } catch (e) { /* stockage indisponible */ } };
  const supporte = () => "Notification" in window;
  const permission = () => supporte() ? Notification.permission : "unsupported";
  const vus = new Set();                        // ids déjà affichés (WebSocket + interrogation de la cloche : une seule notification)

  function etat() {                             // "indisponible" | "bloquee" | "a_demander" | "active" | "coupee"
    const p = permission();
    if (p === "unsupported") return "indisponible";
    if (p === "denied") return "bloquee";
    if (p === "default") return "a_demander";
    return pref() === "off" ? "coupee" : "active";
  }

  function ouvrir(lien) {                       // la coque affiche les pages dans un cadre : on y navigue au lieu de recharger toute la fenêtre
    try {
      const cadre = window.top.document.getElementById("coque-cadre");
      if (cadre) { cadre.src = lien; return; }
    } catch (e) { /* autre origine : repli ci-dessous */ }
    window.location.href = lien;
  }

  function afficher(o) {
    o = o || {};
    if (etat() !== "active") return false;
    if (o.seulement_masquee && !document.hidden) return false;
    if (o.id != null) { if (vus.has(o.id)) return true; vus.add(o.id); if (vus.size > 500) vus.delete(vus.values().next().value); }
    try {
      const n = new Notification(o.titre || T("titre"), {body: o.message || "", tag: o.tag || (o.id != null ? "gy-notif-" + o.id : undefined), renotify: !!(o.tag || o.id != null)});
      n.onclick = () => { try { window.focus(); n.close(); } catch (e) { /* sans effet */ } if (o.lien) ouvrir(o.lien); };
      if (!o.persistante) setTimeout(() => { try { n.close(); } catch (e) { /* déjà fermée */ } }, 12000);
      return true;
    } catch (e) { return false; }               // ex. Chrome Android exige un service worker : l'appelant garde son message à l'écran
  }

  function recevoir(d) {
    if (!d || d.type !== "notification") return false;
    return afficher({id: d.id, titre: d.titre, message: d.message, lien: d.lien});
  }

  function demander() {
    if (!supporte() || Notification.permission !== "default") return Promise.resolve(permission());
    return new Promise(res => {
      const fin = p => { poser(p === "granted" ? "" : "refus"); res(p); };
      try { const r = Notification.requestPermission(fin); if (r && r.then) r.then(fin, () => res(permission())); } catch (e) { res(permission()); }
    });
  }
  const activer = async () => { poser(""); await demander(); return etat(); };
  const desactiver = () => { poser("off"); return etat(); };

  function texte(e) { return e === "active" ? T("actives") : e === "coupee" ? T("coupees") : e === "bloquee" ? T("bloquees") : e === "indisponible" ? T("indisponibles") : T("activer"); }
  function lier(bouton) {                       // bouton « Activer / désactiver » (cloche, page Notifications)
    if (!bouton) return;
    const maj = () => { const e = etat(); bouton.textContent = "🔔 " + texte(e); bouton.disabled = e === "bloquee" || e === "indisponible"; bouton.hidden = e === "indisponible"; };
    bouton.onclick = async () => { const e = etat(); if (e === "active") desactiver(); else if (e === "coupee") poser(""); else await demander(); maj(); };
    maj();
  }

  // Un clic suffit à la première visite : la permission doit être demandée depuis un geste de l'utilisateur (Safari, Firefox).
  if (supporte() && Notification.permission === "default" && pref() !== "refus") {
    document.addEventListener("click", () => { if (Notification.permission === "default") demander(); }, {once: true, capture: true});
  }

  window.GYNotif = {afficher, recevoir, demander, activer, desactiver, etat, lier, supporte};
})();
