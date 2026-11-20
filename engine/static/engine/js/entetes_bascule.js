/* Interrupteur « En-têtes gauche et droite » de l'onglet Action de la barre latérale.
   Allumé : les blocs gauche / droite de l'entête officiel (et la ligne de numérotation, dernière du bloc gauche) sont pris en compte
   dans les documents que la personne génère. Éteint : ils ne le sont pas — les lignes restent enregistrées, le bandeau habituel est utilisé.
   Réglage personnel (administrateur ou agent), enregistré côté serveur : /api/entete-officiel/ { reglages: { entetes_actifs } }. */
(function(){
  const zone = document.getElementById("sidebar-entetes-bascule"), bascule = document.getElementById("toggle-entetes");
  if (!zone || !bascule) return;
  const afficher = actif => bascule.classList.toggle("on", !!actif);

  fetch("/api/entete-officiel/", {headers: {"Accept": "application/json"}})
    .then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(d => { afficher(d.reglages ? d.reglages.entetes_actifs : true); zone.style.display = ""; })
    .catch(() => { /* client (lecteur) ou page sans accès : l'interrupteur reste masqué */ });

  bascule.onclick = async () => {
    const demande = !bascule.classList.contains("on");
    afficher(demande);                                       // retour immédiat ; annulé si le serveur refuse
    try {
      const res = await fetch("/api/entete-officiel/", {
        method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": getCookie("gy_csrftoken")},
        body: JSON.stringify({reglages: {entetes_actifs: demande}})});
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(d.erreur || String(res.status));
      afficher(d.reglages.entetes_actifs);
      notifierSucces(d.reglages.entetes_actifs ? tj("engine/static/engine/js/entetes_bascule.js:on") : tj("engine/static/engine/js/entetes_bascule.js:off"));
    } catch(e){
      afficher(!demande);
      notifierErreur(tj("engine/static/engine/js/entetes_bascule.js:erreur"));
    }
  };
})();
