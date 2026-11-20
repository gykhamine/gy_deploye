// Rappels d'agenda annoncés au client (la cloche de l'en-tête est réservée à l'équipe) : bandeau en haut de page qui mène à /agenda/.
// Interroge /api/agenda/alertes/, qui émet aussi les rappels dus (échéance atteinte, ou dépassée à la reconnexion).
(function(){
  const INTERVALLE_MS = 30000;
  async function verifier(){
    try {
      const rep = await fetch("/api/agenda/alertes/");
      if(!rep.ok) return;
      const d = await rep.json();
      let bandeau = document.getElementById("agenda-alerte");
      if(!d.non_lues){ if(bandeau) bandeau.remove(); return; }
      if(!bandeau){
        bandeau = document.createElement("a");
        bandeau.id = "agenda-alerte"; bandeau.href = "/agenda/";
        bandeau.style.cssText = "display:block;padding:8px 12px;background:var(--accent,#2563eb);color:var(--texte-sombre,#fff);text-align:center;text-decoration:none;font-size:13px;position:relative;z-index:50";
        document.body.prepend(bandeau);
      }
      bandeau.title = tj("engine/static/engine/js/agenda_alerte.js:18:26");
      bandeau.textContent = "⏰ " + d.alertes[0].titre + (d.non_lues > 1 ? " (+" + (d.non_lues - 1) + ")" : "") + " →";
    } catch(e) { /* réseau ou droits : nouvel essai au prochain passage */ }
  }
  verifier();
  setInterval(() => { if(!document.hidden) verifier(); }, INTERVALLE_MS);
  document.addEventListener("visibilitychange", () => { if(!document.hidden) verifier(); });
})();
