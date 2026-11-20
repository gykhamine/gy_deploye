// Couleur configurable pour le canvas (cartes, graphes) : lit la variable CSS posée par /marque.css (page « Personnalisation »).
// gyCouleur("--forme-rouge", "#ff4d4d")       -> couleur choisie, sinon la valeur d'origine.
// gyCouleur("--forme-rouge", "#ff4d4d", 0.12) -> idem avec transparence (rgba).
(function(){
  if(window.gyCouleur) return;
  function rgb(h){
    h = String(h || "").trim();
    if(/^#[0-9a-f]{3}$/i.test(h)) h = "#" + h[1] + h[1] + h[2] + h[2] + h[3] + h[3];
    const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})/i.exec(h);
    return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : null;
  }
  let cacheVar = {}, purge = null;
  window.gyCouleur = function(nom, defaut, alpha){
    let v = cacheVar[nom];                       // une lecture du style par variable et par tâche JS (une image du canevas = une tâche)
    if(v === undefined){
      v = "";
      try { v = getComputedStyle(document.body || document.documentElement).getPropertyValue(nom).trim(); } catch(e) {}
      cacheVar[nom] = v;
      if(!purge) purge = setTimeout(() => { cacheVar = {}; purge = null; }, 0);
    }
    const base = v || defaut;
    if(alpha === undefined || alpha === null) return base;
    const c = rgb(base);
    return c ? "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + alpha + ")" : base;
  };
  // Valeur « #rrggbb » (un champ <input type=color> n'accepte que ce format) : couleur choisie, sinon la valeur d'origine.
  window.gyCouleurHex = function(nom, defaut){
    const c = rgb(window.gyCouleur(nom, defaut));
    return c ? "#" + c.map(x => x.toString(16).padStart(2, "0")).join("") : defaut;
  };
  // Pose la valeur du thème sur un champ couleur resté vide de choix (valeur proposée par défaut).
  window.gyChampCouleur = function(el, nom, defaut){
    if(el) el.value = window.gyCouleurHex(nom, defaut);
  };
  // Couleur de série n° i (0, 1, 2…) : les 8 premières sont réglables (--serie-1…8), au-delà la teinte automatique d'origine.
  const SERIE = ["#cb4d4d", "#cbb04d", "#84cb4d", "#4dcb79", "#4dbbcb", "#4d58cb", "#a54dcb", "#cb4d8e"];
  window.gySerie = function(i){
    i = Math.floor(i);
    return i >= 0 && i < SERIE.length ? window.gyCouleur("--serie-" + (i + 1), SERIE[i]) : "hsl(" + (i * 47 % 360) + ",55%,55%)";
  };
  // Dégradé entre deux couleurs du thème : la fonction rendue lit le thème UNE fois puis donne, pour t de 0 à 1, « rgb(r,g,b) » (ou « rgba(…) » si alpha).
  window.gyDegradeur = function(nomA, defA, nomZ, defZ){
    const a = rgb(window.gyCouleur(nomA, defA)) || rgb(defA), z = rgb(window.gyCouleur(nomZ, defZ)) || rgb(defZ);
    return function(t, alpha){
      t = Math.max(0, Math.min(1, t));
      const c = a.map((x, i) => Math.round(x + (z[i] - x) * t)).join(",");
      return alpha === undefined || alpha === null ? "rgb(" + c + ")" : "rgba(" + c + "," + alpha + ")";
    };
  };
  window.gyDegrade = function(nomA, defA, nomZ, defZ, t, alpha){ return window.gyDegradeur(nomA, defA, nomZ, defZ)(t, alpha); };
  // Champs « couleur » portant data-gy-var="--variable" (et data-gy-defaut="#rrggbb") : leur valeur proposée est celle du thème.
  function initChamps(){
    document.querySelectorAll("input[type=color][data-gy-var]").forEach(el => {
      el.value = window.gyCouleurHex(el.dataset.gyVar, el.dataset.gyDefaut || el.value);
    });
  }
  if(document.readyState === "loading") document.addEventListener("DOMContentLoaded", initChamps); else initChamps();
})();
(function(){
  if(window.gyPalette) return;
  // Palette hexadécimale pour les graphiques : gyPalette(n, decalage) rend n couleurs « #rrggbb » : 24 couleurs choisies à la main
  // (teintes bien séparées, lisibles sur fond clair et sombre), toutes différentes tant que n <= 24 ; au-delà on repart du début de la palette.
  // « decalage » fait démarrer la palette plus loin : deux graphiques de décalages différents n'ont pas les mêmes couleurs.
  const PALETTE = [
    "#e6194b", "#3cb44b", "#f2c500", "#4363d8", "#f58231", "#911eb4", "#17becf", "#f032e6",
    "#8cc63f", "#ff8fa3", "#008b8b", "#b57edc", "#a0522d", "#00a8ff", "#2ecc9a", "#9e9d24",
    "#80cbc4", "#a1887f", "#aa00ff", "#0d47a1", "#ffcc80", "#607d8b", "#bf360c", "#64dd17"
  ];
  window.gyCouleurN = function(k){      // couleur n° k (k entier ≥ 0), la palette se répète tous les 24
    k = Math.max(0, Math.floor(k) || 0);
    return PALETTE[k % PALETTE.length];
  };
  window.gyPalette = function(n, decalage){
    n = Math.max(0, Math.floor(n) || 0); decalage = Math.max(0, Math.floor(decalage) || 0);
    return Array.from({length: n}, (_, i) => window.gyCouleurN(decalage + i));
  };
  // Compteur partagé : gyPaletteSuite(n) rend les n couleurs qui suivent celles déjà données (chaque graphique a donc d'autres couleurs
  // que le précédent) ; gyPaletteRaz() remet le compteur au début (à appeler au début de chaque nouvelle analyse ou nouveau dessin complet).
  let curseur = 0;
  window.gyPaletteSuite = function(n){ const p = window.gyPalette(n, curseur); curseur = (curseur + p.length) % PALETTE.length; return p; };
  window.gyPaletteRaz = function(){ curseur = 0; };
})();
(function(){
  if(window.gyNuance) return;
  // ÉVOLUTION = DÉGRADÉ D'UNE SEULE COULEUR : tout ce qui représente une évolution (courbe dans le temps, cumul, trajectoire, prévision…) part de la
  // nuance la plus SOMBRE de sa couleur (début) et finit à la plus CLAIRE (fin). Même teinte et même saturation, seule la luminosité change.
  // Les bornes évitent le noir et le blanc purs : la courbe reste lisible sur fond clair comme sur fond sombre.
  const L_SOMBRE = 0.26, L_CLAIR = 0.80;
  function lireRgb(couleur){
    const c = String(couleur || "").trim();
    let m = /^#([0-9a-f]{3})$/i.exec(c);
    if(m) return [parseInt(m[1][0] + m[1][0], 16), parseInt(m[1][1] + m[1][1], 16), parseInt(m[1][2] + m[1][2], 16)];
    m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})/i.exec(c);
    if(m) return [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)];
    m = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i.exec(c);
    if(m) return [Math.round(+m[1]), Math.round(+m[2]), Math.round(+m[3])];
    m = /^hsla?\(\s*([\d.]+)(?:deg)?[\s,]+([\d.]+)%[\s,]+([\d.]+)%/i.exec(c);
    if(m){
      const h = (+m[1] % 360) / 60, s = +m[2] / 100, l = +m[3] / 100, k = (1 - Math.abs(2 * l - 1)) * s, x = k * (1 - Math.abs(h % 2 - 1));
      const [r, g, b] = h < 1 ? [k, x, 0] : h < 2 ? [x, k, 0] : h < 3 ? [0, k, x] : h < 4 ? [0, x, k] : h < 5 ? [x, 0, k] : [k, 0, x], d = l - k / 2;
      return [Math.round((r + d) * 255), Math.round((g + d) * 255), Math.round((b + d) * 255)];
    }
    return null;
  }
  function teinte(rgb){   // [r, v, b] 0-255 -> [teinte 0-360, saturation 0-1]
    const r = rgb[0] / 255, v = rgb[1] / 255, b = rgb[2] / 255, mx = Math.max(r, v, b), mn = Math.min(r, v, b), d = mx - mn, l = (mx + mn) / 2;
    let h = 0, s = 0;
    if(d){
      s = d / (1 - Math.abs(2 * l - 1));
      h = mx === r ? ((v - b) / d) % 6 : mx === v ? (b - r) / d + 2 : (r - v) / d + 4;
      h = (h * 60 + 360) % 360;
    }
    return [h, Math.min(1, s)];
  }
  // Nuance de « couleur » pour t de 0 (le plus sombre) à 1 (le plus clair). Couleur illisible (var(--x), nom CSS) : rendue telle quelle.
  window.gyNuance = function(couleur, t, alpha){
    const rgb = lireRgb(couleur);
    if(!rgb) return couleur;
    t = Math.max(0, Math.min(1, +t || 0));
    const hs = teinte(rgb), l = L_SOMBRE + (L_CLAIR - L_SOMBRE) * t;
    const base = Math.round(hs[0]) + "," + Math.round(hs[1] * 100) + "%," + Math.round(l * 100) + "%";
    return alpha === undefined || alpha === null ? "hsl(" + base + ")" : "hsla(" + base + "," + alpha + ")";
  };
  // n nuances de la même couleur, de la plus sombre à la plus claire.
  window.gyNuances = function(couleur, n){
    n = Math.max(1, Math.floor(n) || 1);
    return Array.from({length: n}, (_, i) => window.gyNuance(couleur, n === 1 ? 0.5 : i / (n - 1)));
  };
  // Fond CSS (puce de légende) : le dégradé complet de la couleur.
  window.gyFondDegrade = function(couleur){
    return "linear-gradient(90deg," + window.gyNuance(couleur, 0) + "," + window.gyNuance(couleur, 1) + ")";
  };
  // Courbe SVG dégradée : points = [[x, y] | null, …] dans l'ordre de l'évolution (null = trou). Rend le balisage « <defs>…<path/> » (chaîne),
  // à mettre dans un <svg>. Le dégradé suit l'axe horizontal du premier au dernier point ; attrs = autres attributs du <path>
  // (stroke-width, stroke-dasharray…). Un seul <path> : les pointillés restent réguliers, quel que soit le nombre de points.
  let compteur = 0;
  const bon = p => p && isFinite(p[0]) && isFinite(p[1]);
  window.gyLigneDegradeMarkup = function(points, couleur, attrs){
    const valides = (points || []).filter(bon);
    if(!valides.length) return "";
    let d = "", plume = false;
    for(const p of points){
      if(!bon(p)){ plume = false; continue; }
      d += (plume ? "L" : "M") + (+p[0]).toFixed(1) + " " + (+p[1]).toFixed(1);
      plume = true;
    }
    const autres = Object.entries(attrs || {}).map(([k, v]) => " " + k + '="' + String(v).replace(/"/g, "&quot;") + '"').join("");
    const x1 = +valides[0][0], x2 = +valides[valides.length - 1][0];
    if(Math.abs(x2 - x1) < 1e-6) return '<path d="' + d + '" fill="none" stroke="' + window.gyNuance(couleur, 0.5) + '"' + autres + "/>";
    const id = "gyDeg" + (++compteur);
    const arrets = Array.from({length: 10}, (_, i) => '<stop offset="' + (i / 9) + '" stop-color="' + window.gyNuance(couleur, i / 9) + '"/>').join("");
    return '<defs><linearGradient id="' + id + '" gradientUnits="userSpaceOnUse" x1="' + x1 + '" y1="0" x2="' + x2 + '" y2="0">' + arrets + "</linearGradient></defs>"
      + '<path d="' + d + '" fill="none" stroke="url(#' + id + ')"' + autres + "/>";
  };
  // Même courbe en élément SVG <g> (à ajouter avec svg.append).
  window.gyLigneDegrade = function(points, couleur, attrs){
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.innerHTML = window.gyLigneDegradeMarkup(points, couleur, attrs);
    return g;
  };
})();
