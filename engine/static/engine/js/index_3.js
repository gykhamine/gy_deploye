(function(){
  const tip = document.getElementById("guide-tooltip");
  let _t;
  // Écouteurs en phase de capture sur document : la cible peut être le document lui-même (pas d'élément,
  // donc pas de .closest) — l'erreur « e.target.closest is not a function » sortait à chaque entrée de souris.
  const cible = e => (e.target instanceof Element ? e.target : null);
  document.addEventListener("mouseenter", e => {
    const el = cible(e) && cible(e).closest("[data-guide]");
    if(!el) return;
    clearTimeout(_t);
    tip.textContent = el.dataset.guide;
    const r = el.getBoundingClientRect();
    tip.style.top  = Math.min(r.bottom + 6, window.innerHeight - 120) + "px";
    tip.style.left = Math.max(4, Math.min(r.left, window.innerWidth - 270)) + "px";
    tip.classList.add("visible");
  }, true);
  document.addEventListener("mouseleave", e => {
    if(cible(e) && cible(e).closest("[data-guide]")){ clearTimeout(_t); _t = setTimeout(() => tip.classList.remove("visible"), 80); }
  }, true);
  document.addEventListener("scroll", () => tip.classList.remove("visible"), true);
})();
