// Cloche de notifications (page principale, à côté du bouton de profil) : pastille du nombre de non-lues, liste
// déroulante des plus récentes, « Tout marquer comme lu ». Réutilise l'API existante /api/notifications/ (la même que
// la page Notifications). Démarrée par chargerProfil() (index_1.js) pour les agents/admin uniquement.
(function(){
  const INTERVALLE_MS = 30000, MAX_AFFICHEES = 15;
  let demarree = false, vues = null;   // vues : ids déjà connus (pour ne signaler par un toast que les NOUVELLES)

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const csrf = () => (document.cookie.split("; ").find(c => c.startsWith("gy_csrftoken=")) || "").split("=")[1] || "";
  const ICONES = {kpi_objectif: "🎯", kpi_alerte: "⚠️", depot_verifie: "💰", nouvelle_ligne_client: "📝", tache_reussie: "✅", tache_echec: "❌", recommandation: "💡", decision_strategique: "🧭", geo_rencontre: "📍", geo_collision: "🚨", agenda_rappel: "⏰", agenda_retard: "⚠️", agenda_retour: "📅"};

  function depuis(iso){
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if(s < 60) return tj("engine/static/engine/js/notifications_cloche.js:15:24");
    if(s < 3600) return tj("engine/static/engine/js/notifications_cloche.js:16:26", {v1: Math.floor(s / 60)});
    if(s < 86400) return tj("engine/static/engine/js/notifications_cloche.js:17:27", {v1: Math.floor(s / 3600)});
    return new Date(iso).toLocaleDateString((window.I18N_LOCALE||"fr-FR"), {day: "2-digit", month: "2-digit"});
  }

  async function appel(url, options){
    const rep = await fetch(url, Object.assign({headers: {"Content-Type": "application/json", "X-CSRFToken": csrf()}}, options || {}));
    if(!rep.ok) throw new Error(String(rep.status));
    return rep.json();
  }

  function majBadge(nonLues){
    const b = $("notif-badge");
    b.textContent = nonLues > 99 ? "99+" : String(nonLues);
    b.style.display = nonLues > 0 ? "" : "none";
    $("notif-cloche").classList.toggle("a-des-non-lues", nonLues > 0);
    $("notif-cloche").title = nonLues > 0 ? `${tj("engine/static/engine/js/notifications_cloche.js:32:46", {v1: nonLues})}` : tj("engine/static/engine/js/notifications_cloche.js:32:88");
  }

  function dessiner(data){
    const liste = $("notif-liste"), notifs = data.notifications.slice(0, MAX_AFFICHEES);
    majBadge(data.non_lues);
    $("notif-tout-lire").disabled = data.non_lues === 0;
    $("notif-effacer").disabled = data.notifications.length === 0;
    if(!notifs.length){ liste.innerHTML = `<div class="np-vide">${tj("engine/static/engine/js/notifications_cloche.js:39:66")}</div>`; return; }
    liste.innerHTML = notifs.map(n => `
      <div class="np-item ${n.lue ? "lue" : "non-lue"}" data-id="${n.id}" data-lien="${esc(n.lien || "")}" role="button" tabindex="0">
        <span class="np-icone">${ICONES[n.type] || "🔔"}</span>
        <span class="np-corps">
          <span class="np-titre">${esc(n.titre)}</span>
          <span class="np-message">${esc(n.message)}</span>
          <span class="np-date">${esc(depuis(n.cree_le))}</span>
        </span>
        ${n.lue ? "" : `<span class="np-puce" aria-label="${tj("engine/static/engine/js/notifications_cloche.js:48:60")}"></span>`}
      </div>`).join("");
  }

  async function charger(){
    try {
      const data = await appel(`/api/notifications/?limite=${MAX_AFFICHEES}`);
      // Toast pour les non-lues apparues depuis le dernier passage (rien au tout premier chargement de la page).
      if(vues !== null && typeof notifier === "function"){
        data.notifications.filter(n => !n.lue && !vues.has(n.id)).slice(0, 3).forEach(n => {
          const affichee = window.GYNotif && GYNotif.afficher({id: n.id, titre: n.titre, message: n.message, lien: n.lien, type: n.type});
          if(!affichee) notifier(`🔔 ${n.titre}`, "succes");   // pas de permission : le message reste à l'écran
        });
      }
      vues = new Set([...(vues || []), ...data.notifications.map(n => n.id)]);
      dessiner(data);
    } catch(e) { /* réseau ou droits : la cloche garde son dernier état, nouvel essai au prochain passage */ }
  }

  function placerPanneau(){
    // Panneau en position fixe : l'en-tête a overflow:hidden, un panneau absolu y serait coupé.
    const r = $("notif-cloche").getBoundingClientRect(), p = $("notif-panneau");
    p.style.top = (r.bottom + 6) + "px";
    p.style.right = Math.max(8, window.innerWidth - r.right) + "px";
  }
  function ouvrir(){ placerPanneau(); $("notif-panneau").style.display = "flex"; $("notif-cloche").setAttribute("aria-expanded", "true"); charger(); }
  function fermer(){ $("notif-panneau").style.display = "none"; $("notif-cloche").setAttribute("aria-expanded", "false"); }

  async function marquerLue(id){
    try { await appel(`/api/notifications/${id}/lire/`, {method: "POST"}); } catch(e) { /* sans gravité */ }
  }

  window.demarrerClocheNotifications = function(){
    if(demarree || !$("notif-cloche")) return;
    demarree = true;
    if(window.GYNotif) GYNotif.lier($("notif-navigateur"));
    $("notif-cloche-wrap").style.display = "";
    $("notif-cloche").onclick = (ev) => { ev.stopPropagation(); $("notif-panneau").style.display === "flex" ? fermer() : ouvrir(); };
    $("notif-panneau").onclick = (ev) => ev.stopPropagation();
    document.addEventListener("click", fermer);
    document.addEventListener("keydown", (ev) => { if(ev.key === "Escape") fermer(); });
    window.addEventListener("resize", () => { if($("notif-panneau").style.display === "flex") placerPanneau(); });
    $("notif-tout-lire").onclick = async () => {
      try { await appel("/api/notifications/tout-lire/", {method: "POST"}); } catch(e) { return; }
      charger();
    };
    $("notif-effacer").onclick = async () => {
      if(!await GY.confirmer(tj("engine/static/engine/js/notifications_cloche.js:effacer_confirmer"))) return;
      try { await appel("/api/notifications/effacer/", {method: "POST"}); } catch(e) { return; }
      vues = new Set();
      charger();
    };
    $("notif-liste").onclick = async (ev) => {
      const item = ev.target.closest(".np-item");
      if(!item) return;
      if(item.classList.contains("non-lue")) await marquerLue(item.dataset.id);
      if(item.dataset.lien){ window.location.href = item.dataset.lien; return; }
      charger();
    };
    charger();
    setInterval(() => { if(!document.hidden) charger(); }, INTERVALLE_MS);
    document.addEventListener("visibilitychange", () => { if(!document.hidden) charger(); });
  };
})();
