// Plein écran réel d'UN élément (API Fullscreen), commun à toutes les pages qui l'offrent (ontologie, BI, géolocalisation) :
// seul l'élément reste à l'écran — en-tête (logo, onglets), barre latérale, coque et barre de l'application disparaissent.
// Le bouton « quitter » est déplacé DANS l'élément le temps du plein écran (hors de lui il serait masqué). Échap = natif.
// Repli si l'API est absente ou refusée : ancien comportement (classe body.plein-ecran, masquage par CSS).
window.GYPleinEcran = (function(){
  let el = null, btn = null, origine = null, rappel = null;
  const natif = () => document.fullscreenElement || document.webkitFullscreenElement;
  function etat(actif){
    document.body.classList.toggle("plein-ecran", actif);
    if(btn){
      if(actif && el && btn.parentNode !== el){ origine = btn.parentNode; el.appendChild(btn); }
      else if(!actif && origine){ origine.appendChild(btn); origine = null; }
    }
    requestAnimationFrame(() => { window.dispatchEvent(new Event("resize")); if(rappel) rappel(actif); });
  }
  function surChangement(){ etat(!!el && natif() === el); }
  document.addEventListener("fullscreenchange", surChangement);
  document.addEventListener("webkitfullscreenchange", surChangement);
  document.addEventListener("keydown", e => { if(e.key === "Escape" && !natif() && document.body.classList.contains("plein-ecran")) etat(false); });
  function quitter(){
    if(natif()){ (document.exitFullscreen || document.webkitExitFullscreen).call(document); }
    else if(document.body.classList.contains("plein-ecran")) etat(false);
  }
  function basculer(){
    if(natif() || document.body.classList.contains("plein-ecran")) return quitter();
    const demande = el && (el.requestFullscreen || el.webkitRequestFullscreen);
    if(!demande) return etat(true);
    try {
      const p = demande.call(el);
      if(p && p.catch) p.catch(() => etat(true));
    } catch(e){ etat(true); }
  }
  return {
    // element : ce qui reste affiché ; boutonQuitter : ✕ (optionnel) ; surEtat(actif) : rappel après chaque bascule.
    init(element, boutonQuitter, surEtat){
      el = element; btn = boutonQuitter || null; rappel = surEtat || null;
      if(btn) btn.onclick = quitter;
    },
    basculer, quitter,
  };
})();
