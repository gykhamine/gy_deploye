/* Double-toucher → « dblclick » (écran tactile et stylet ; la souris n'est pas touchée).
   Les pages écoutent déjà « dblclick » (édition d'une cellule, fiche d'un nœud, zoom de la carte…), mais les navigateurs
   tactiles ne l'envoient pas de façon fiable (iOS Safari, Chrome avec zoom au double-toucher). Ce fichier traduit le geste
   en l'événement que les pages attendent déjà, une fois le 2e « click » passé (même ordre qu'avec une souris). */
(function(){
  "use strict";
  if(window.__gyGestesTactiles) return;   // page chargée deux fois (fragment injecté) : un seul jeu d'écouteurs
  window.__gyGestesTactiles = true;
  var DELAI = 350, DISTANCE = 24, BOUGE = 10;
  var FORMULAIRE = "input, textarea, select, button, a, label, [contenteditable]";
  var depart = null, dernier = null, attente = null, synthetique = 0;

  function lancer(){
    if(!attente) return;
    var a = attente; attente = null; clearTimeout(a.minuterie);
    synthetique = Date.now();
    if(!a.cible.isConnected) return;
    a.cible.dispatchEvent(new MouseEvent("dblclick", {bubbles: true, cancelable: true, composed: true, view: window,
      clientX: a.x, clientY: a.y, screenX: a.sx, screenY: a.sy, detail: 2}));
  }

  document.addEventListener("pointerdown", function(e){
    if(e.pointerType === "mouse" || !e.isPrimary){ depart = null; return; }
    depart = {x: e.clientX, y: e.clientY};
  }, true);
  document.addEventListener("pointercancel", function(){ depart = null; dernier = null; }, true);
  document.addEventListener("pointerup", function(e){
    if(e.pointerType === "mouse" || !e.isPrimary || !depart) return;
    var d = depart; depart = null;
    if(Math.hypot(e.clientX - d.x, e.clientY - d.y) > BOUGE){ dernier = null; return; }   // un glissé n'est pas un toucher
    var cible = e.target, t = Date.now();
    if(!cible || !cible.closest || cible.closest(FORMULAIRE)){ dernier = null; return; }
    if(dernier && t - dernier.t < DELAI && Math.hypot(e.clientX - dernier.x, e.clientY - dernier.y) < DISTANCE){
      dernier = null;
      attente = {cible: cible, x: e.clientX, y: e.clientY, sx: e.screenX, sy: e.screenY, minuterie: setTimeout(lancer, 450)};   // secours : pas de « click » (page qui bloque le toucher)
    } else {
      dernier = {t: t, x: e.clientX, y: e.clientY};
    }
  }, true);
  window.addEventListener("click", function(){ if(attente) setTimeout(lancer, 0); }, true);
  // Le « dblclick » natif d'un navigateur qui l'envoie quand même est écarté : un seul double-clic par double-toucher.
  document.addEventListener("dblclick", function(e){
    if(e.isTrusted && (attente || Date.now() - synthetique < 800)){ e.stopImmediatePropagation(); e.preventDefault(); }
  }, true);
})();
