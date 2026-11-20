// Page blockchain — couleur d'un bloc (🎨 dans son en-tête) ou de tous les blocs d'un type de transaction (barre « Couleur par type »).
// Même fenêtre de choix que les colonnes (GYCouleur, selecteur_couleur.js). La couleur est enregistrée côté serveur (CouleurBloc) :
// elle est gardée et se retrouve dans toutes les portées (globale, portable, agent) et sous tous les filtres.
(function(){
  const csrf = () => { const m = document.cookie.match(/(?:^|; )gy_csrftoken=([^;]+)/); return m ? decodeURIComponent(m[1]) : ""; };
  async function envoyer(corps){
    const r = await fetch("/api/blockchain/couleur/", {method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRFToken": csrf()}, body: JSON.stringify(corps)});
    const d = await r.json().catch(() => ({}));
    if(!r.ok) throw new Error(d.erreur || ("HTTP " + r.status));
    return d;
  }
  document.addEventListener("click", async (e) => {
    const bouton = e.target.closest(".btn-couleur-bloc");
    if(!bouton || !window.GYCouleur) return;
    const parType = bouton.id === "coul-type-btn";
    const select = parType ? document.getElementById("coul-type") : null;
    const type = select ? select.value : "";
    const valeur = parType ? ((select.selectedOptions[0] || {dataset: {}}).dataset.couleur || "") : (bouton.dataset.couleur || "");
    const choix = await GYCouleur.choisir({titre: parType ? tj("engine/static/engine/js/blockchain_couleurs.js:20:62") + type + " »" : tj("engine/static/engine/js/blockchain_couleurs.js:20:101") + bouton.dataset.bloc, valeur, retirable: true});
    if(choix === null) return;   // fenêtre fermée
    try {
      await envoyer(parType ? {type, couleur: choix} : {bloc: Number(bouton.dataset.bloc), couleur: choix});
      location.reload();
    } catch(err) { GY.alerter(err.message); }
  });
})();
