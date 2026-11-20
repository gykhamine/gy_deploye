// Bascule de la barre latérale des pages Ontologie et BI (même comportement que la page principale, en plus robuste).
// - UN SEUL bouton ☰ : celui de l'en-tête (#btn-toggle-sidebar), toujours visible, sidebar affichée ou masquée.
// - PC (> 768 px) : masque / affiche la sidebar ; l'état est mémorisé par page (localStorage, jamais bloquant).
// - Mobile (<= 768 px) : tiroir + voile, comme la page principale.
// - Raccourci Ctrl+B.
// - Déclenche « resize » après chaque bascule : le graphe (canvas) et la grille BI se recalculent sur la largeur libérée.
(function(){
  const bouton = document.getElementById('btn-toggle-sidebar');
  const barre = document.getElementById('sidebar');
  const voile = document.getElementById('sidebar-overlay');
  if(!barre) return;
  const CLE = 'gy_sidebar_masque_' + (document.body.className.match(/page-[\w-]+/) || ['page'])[0];
  const mobile = () => window.innerWidth <= 768;

  function memoriser(masque){
    try { localStorage.setItem(CLE, masque ? '1' : '0'); } catch(e) {}
  }
  function fermerTiroir(){
    barre.classList.remove('ouvert');
    if(voile) voile.classList.remove('ouvert');
  }
  function basculer(){
    if(mobile()){
      const ouvert = barre.classList.toggle('ouvert');
      if(voile) voile.classList.toggle('ouvert', ouvert);
      return;
    }
    memoriser(barre.classList.toggle('masque'));
    window.dispatchEvent(new Event('resize'));
  }

  try { if(localStorage.getItem(CLE) === '1' && !mobile()) barre.classList.add('masque'); } catch(e) {}
  if(bouton) bouton.addEventListener('click', (e) => { e.stopPropagation(); basculer(); });
  if(voile) voile.addEventListener('click', fermerTiroir);
  document.addEventListener('keydown', (e) => {
    if((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'b'){
      e.preventDefault();
      basculer();
    }
  });
  window.addEventListener('resize', () => { if(!mobile()) fermerTiroir(); });
  window.dispatchEvent(new Event('resize'));
})();
