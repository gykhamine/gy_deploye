// Bascule de la colonne latérale des pages IA, Messagerie et Documents administratifs (leur colonne de gauche est un <aside>,
// sans barre latérale « application »).
// - L'<aside> porte data-colonne-laterale ; data-libelle = texte traduit du bouton (fourni par le gabarit).
// - Le bouton ☰ est créé ici, en tête du bandeau (.header), avant le titre.
// - PC et mobile : masque / affiche la colonne ; l'état est mémorisé par page (localStorage, jamais bloquant).
// - Raccourci Ctrl+B, comme sur les autres pages.
// - Déclenche « resize » après chaque bascule : les graphiques se recalculent sur la largeur libérée.
// - Page à deux écrans (Documents administratifs) : data-colonne-inverse = classe du <body> de l'écran où la colonne est cachée par défaut
//   (édition d'un document). Sur cet écran, le bouton ouvre la colonne à côté du document (classe gy-colonne-ouverte, mémorisée) ;
//   sur l'autre écran (la liste est le contenu), il la masque pour la session seulement (classe gy-colonne-masquee, non mémorisée).
(function(){
  const colonne = document.querySelector('[data-colonne-laterale]');
  const entete = document.querySelector('.header');
  if(!colonne || !entete || document.getElementById('btn-colonne')) return;
  const conteneur = colonne.parentElement;
  const CLE = 'gy_colonne_masquee_' + location.pathname;
  const inverse = colonne.getAttribute('data-colonne-inverse') || '';
  const libelle = colonne.getAttribute('data-libelle') || '';
  if(!colonne.id) colonne.id = 'colonne-laterale';

  const bouton = document.createElement('button');
  bouton.type = 'button';
  bouton.id = 'btn-colonne';
  bouton.className = 'btn-colonne';
  bouton.textContent = '☰';
  bouton.setAttribute('aria-controls', colonne.id);
  if(libelle){ bouton.setAttribute('aria-label', libelle); bouton.title = libelle; }
  entete.insertBefore(bouton, entete.firstChild);

  // Mode « tiroir » (data-colonne-tiroir) : sur petit écran la colonne est un tiroir posé sur le contenu, fermé au départ, jamais mémorisé,
  // refermé par un toucher sur le voile ou par window.gyFermerColonne() ; sur grand écran, comportement habituel (colonne à côté du contenu).
  const tiroir = colonne.hasAttribute('data-colonne-tiroir') && !!window.matchMedia;
  const mq = tiroir ? window.matchMedia('(max-width:760px)') : null;
  const etroit = () => !!mq && mq.matches;
  const enInverse = () => (!!inverse && document.body.classList.contains(inverse)) || etroit();
  const visible = () => enInverse() ? conteneur.classList.contains('gy-colonne-ouverte') : !conteneur.classList.contains('gy-colonne-masquee');
  // Le CSS de la page (ex. #msg-gauche{display:flex}) l'emporte sur la règle « display:none » de la classe de masquage :
  // la colonne ne disparaissait pas, elle changeait seulement de forme (empilée au-dessus). On la cache donc directement ici.
  const synchroniser = () => {
    const v = visible();
    bouton.setAttribute('aria-expanded', v ? 'true' : 'false');
    if(v) colonne.style.removeProperty('display');
    else colonne.style.setProperty('display', 'none', 'important');
  };
  function lire(cle){ try { return localStorage.getItem(cle) === '1'; } catch(e) { return false; } }
  function memoriser(cle, valeur){ try { localStorage.setItem(cle, valeur ? '1' : '0'); } catch(e) {} }

  function basculer(){
    if(enInverse()){
      const ouverte = conteneur.classList.toggle('gy-colonne-ouverte');
      if(!etroit()) memoriser(CLE + ':ouverte', ouverte);
    } else {
      const masquee = conteneur.classList.toggle('gy-colonne-masquee');
      if(!inverse) memoriser(CLE, masquee);   // page à deux écrans : le masquage de la liste n'est jamais mémorisé
    }
    if(!visible() && colonne.contains(document.activeElement)) bouton.focus();   // le focus ne reste pas dans une colonne cachée
    synchroniser();
    window.dispatchEvent(new Event('resize'));
  }

  if(inverse) conteneur.classList.toggle('gy-colonne-ouverte', lire(CLE + ':ouverte'));
  else conteneur.classList.toggle('gy-colonne-masquee', lire(CLE));
  synchroniser();

  // Changement d'écran (liste <-> document) : on repart d'une liste visible et on remet l'état du bouton à jour.
  if(inverse && window.MutationObserver){
    new MutationObserver(() => { conteneur.classList.remove('gy-colonne-masquee'); synchroniser(); })
      .observe(document.body, {attributes: true, attributeFilter: ['class']});
  }

  bouton.addEventListener('click', basculer);
  if(tiroir){
    const voile = document.createElement('div');
    voile.className = 'gy-colonne-voile';
    voile.addEventListener('click', () => { if(etroit() && visible()) basculer(); });
    conteneur.appendChild(voile);
    window.gyFermerColonne = () => { if(etroit() && visible()) basculer(); };
    const changement = () => { conteneur.classList.remove('gy-colonne-ouverte'); conteneur.classList.toggle('gy-colonne-masquee', !etroit() && lire(CLE)); synchroniser(); window.dispatchEvent(new Event('resize')); };
    if(mq.addEventListener) mq.addEventListener('change', changement); else if(mq.addListener) mq.addListener(changement);
    changement();   // état initial : tiroir fermé sur petit écran, colonne mémorisée sur grand écran
  }
  document.addEventListener('keydown', (e) => {
    if((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'b'){
      e.preventDefault();
      basculer();
    }
  });
})();
