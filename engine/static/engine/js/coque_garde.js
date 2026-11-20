// Garde de la coque : une page ouverte seule (hors cadre) est rechargée DANS la coque, pour que l'appel en cours survive à la navigation.
// Échappatoire : ajouter ?sans_coque=1 à l'adresse.
if (window.top === window && !/[?&]sans_coque=1/.test(location.search)) {
  location.replace(document.currentScript.dataset.coque + "?p=" + encodeURIComponent(location.pathname + location.search + location.hash));
}
