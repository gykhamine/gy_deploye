/* Aperçu des fichiers lisibles (PDF, texte) : même principe que l'aperçu PDF de la page « Documents administratifs »
   (un cadre <iframe> sur le fichier servi « inline »), étendu aux fichiers texte (affichés dans un bloc, sans exécuter quoi que ce soit).
   Partagé par les tables dynamiques (agent : index.html ; client : client.html) et par les publicités (agent et client).

   ApercuFichier.genre(nomOuUrl)      → "pdf" | "texte" | ""   (d'après l'extension ; "" = pas d'aperçu intégré)
   ApercuFichier.monter(zone, url, g) → remplit `zone` avec le cadre PDF ou le bloc de texte ; ApercuFichier.vider(zone) le retire.
   ApercuFichier.bouton(url, titre)   → HTML du bouton « Aperçu » d'une cellule (vide si pas d'aperçu possible).
   Tous les textes passent par tj() (catalogues fr/en) : la page doit contenir {% i18n_js %}. */
(function(){
  const EXT_PDF = new Set(["pdf"]);
  const EXT_OFFICE = new Set(["docx", "doc", "pptx", "ppt", "odt", "odp"]);   // affichés comme un PDF (converti par le serveur si LibreOffice est installé)
  const EXT_DIAPO = new Set(["pptx", "ppt", "odp"]);   // lus en vidéo (durées et transitions), repli sur le PDF
  // Tout ce qui se lit sans risque : texte brut, données, balisage affiché comme du TEXTE (jamais interprété), journaux, sous-titres, configurations.
  const EXT_TEXTE = new Set(["txt", "text", "md", "markdown", "rst", "adoc", "org", "csv", "tsv", "json", "jsonl", "ndjson", "geojson", "xml", "gpx", "kml", "ics", "vcf", "log", "yml", "yaml", "toml", "ini", "cfg", "conf", "properties", "srt", "vtt", "tex", "bib", "diff", "patch", "nfo", "readme"]);
  // Texte qui PEUT S'EXÉCUTER (scripts, pages, programmes) : jamais d'aperçu ; ces fichiers ne passent que par un zip (contrôle serveur à l'envoi).
  const EXT_EXECUTABLE = new Set(["html", "htm", "xhtml", "shtml", "js", "mjs", "jse", "ts", "jsx", "py", "pyw", "sh", "bash", "zsh", "bat", "cmd", "ps1", "vbs", "vbe", "wsf", "php", "phtml", "phar", "pl", "rb", "lua", "swf", "hta", "jar", "exe", "dll", "msi", "com", "scr", "apk", "lnk", "svg", "sql"]);

  function extension(nomOuUrl){
    let s = String(nomOuUrl || "").split("#")[0].split("?")[0];
    try { s = decodeURIComponent(s); } catch(e){ /* nom non encodé : tel quel */ }
    const base = s.split("/").pop().split("\\").pop();
    const i = base.lastIndexOf(".");
    return i > 0 ? base.slice(i + 1).toLowerCase() : "";
  }

  function genre(nomOuUrl){
    const e = extension(nomOuUrl);
    if (EXT_EXECUTABLE.has(e)) return "";
    return EXT_PDF.has(e) || EXT_OFFICE.has(e) ? "pdf" : EXT_TEXTE.has(e) ? "texte" : "";
  }


  /* Lecteur PDF intégré (PDF.js, embarqué dans static/engine/vendor/pdfjs : aucun CDN, fonctionne hors ligne).
     Pourquoi : Safari iOS et Chrome Android n'affichent PAS un PDF dans un <iframe> : ils montrent une carte « Ouvrir / Télécharger ».
     Ici les pages sont dessinées dans des <canvas> : le document se LIT dans la page, sur tous les appareils.
     Les adresses viennent de la page (window.APERCU_PDFJS = {lib, worker, base}, fournies par {% static %}) ; sans elles, ou en cas
     d'échec, on retombe sur l'ancien cadre <iframe>. */
  let _promessePdfjs = null;
  function chargerPdfjs(){
    const c = window.APERCU_PDFJS;
    if (!c || !c.lib) return Promise.reject(new Error("pdfjs"));
    if (!_promessePdfjs) {
      _promessePdfjs = import(c.lib).then(m => { m.GlobalWorkerOptions.workerSrc = c.worker; return m; })
        .catch(e => { _promessePdfjs = null; throw e; });
    }
    return _promessePdfjs;
  }

  function lirePdf(zone, src, jeton, repli){
    const vivant = () => zone._jeton === jeton;
    const defilement = document.createElement("div");
    defilement.style.cssText = "width:100%;max-height:78vh;overflow:auto;-webkit-overflow-scrolling:touch;box-sizing:border-box;padding:8px 0;background:var(--panel,#e5e5e5);text-align:center";
    defilement.textContent = tj("engine/static/engine/js/apercu_fichier.js:g:chargement");
    zone.appendChild(defilement);
    let doc = null, tache = null, observateur = null, largeurRendue = 0;
    zone._fin = () => { if (observateur) observateur.disconnect(); if (tache) tache.destroy(); if (doc) doc.destroy(); };
    const echec = () => { if (!vivant()) return; zone._fin(); zone._fin = null; zone.textContent = ""; repli(); };

    chargerPdfjs().then(pdfjs => {
      tache = pdfjs.getDocument({url: src, withCredentials: true, standardFontDataUrl: window.APERCU_PDFJS.base + "standard_fonts/", cMapUrl: window.APERCU_PDFJS.base + "cmaps/", cMapPacked: true});
      return tache.promise;
    }).then(d => {
      if (!vivant()) { d.destroy(); return; }
      doc = d;
      const mettre = () => {
        if (observateur) observateur.disconnect();
        defilement.textContent = "";
        const largeur = Math.max(200, defilement.clientWidth - 16);
        largeurRendue = defilement.clientWidth;
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        observateur = new IntersectionObserver(entrees => entrees.forEach(en => {
          if (!en.isIntersecting || !vivant()) return;
          const cv = en.target; observateur.unobserve(cv);
          doc.getPage(cv._n).then(page => {
            if (!vivant() || !cv.isConnected) return;
            const base = page.getViewport({scale: 1}), echelle = largeur / base.width, vue = page.getViewport({scale: echelle * ratio});
            cv.width = Math.floor(vue.width); cv.height = Math.floor(vue.height);
            return page.render({canvasContext: cv.getContext("2d"), viewport: vue, canvas: cv}).promise;
          }).catch(() => { /* une page illisible n'empêche pas de lire les autres */ });
        }), {root: defilement, rootMargin: "800px 0px"});
        const creees = [];
        for (let n = 1; n <= doc.numPages; n++) {
          const cv = document.createElement("canvas"); cv._n = n;
          cv.style.cssText = "display:block;margin:0 auto 8px;max-width:100%;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.25)";
          cv.style.width = largeur + "px";
          cv.style.height = Math.round(largeur * 1.414) + "px";   // hauteur provisoire (A4) : remplacée par le vrai format au rendu
          defilement.appendChild(cv); creees.push(cv);
          doc.getPage(n).then(pg => { const b = pg.getViewport({scale: 1}); cv.style.height = Math.round(largeur * b.height / b.width) + "px"; observateur.observe(cv); });
        }
      };
      mettre();
      if (window.ResizeObserver) {   // rotation de l'écran, redimensionnement : on redessine à la nouvelle largeur
        let minuterie = null;
        const ro = new ResizeObserver(() => { clearTimeout(minuterie); minuterie = setTimeout(() => { if (vivant() && Math.abs(defilement.clientWidth - largeurRendue) > 24) mettre(); }, 250); });
        ro.observe(defilement);
        const fin = zone._fin; zone._fin = () => { ro.disconnect(); clearTimeout(minuterie); fin(); };
      }
    }).catch(echec);
  }

  function vider(zone){
    if (!zone) return;
    zone._jeton = (zone._jeton || 0) + 1;   // une lecture de texte encore en cours ne doit plus rien écrire ici
    if (zone._fin) { try { zone._fin(); } catch(e){ /* déjà libéré */ } zone._fin = null; }
    zone.textContent = "";
  }

  function monter(zone, url, g){
    if (!zone) return;
    vider(zone);
    const jeton = zone._jeton;
    if (EXT_EXECUTABLE.has(extension(url))) return;   // même si un appelant impose le genre : un texte exécutable n'est jamais affiché
    g = g || genre(url);
    if (g === "pdf") {
      const office = EXT_OFFICE.has(extension(url));
      const src = office ? url + (url.includes("?") ? "&" : "?") + "apercu=pdf" : url;
      const cadreIframe = () => {   // repli : cadre du navigateur (fonctionne sur ordinateur ; sur mobile il peut proposer « Ouvrir »)
        const cadre = document.createElement("iframe");
        cadre.title = tj("engine/static/engine/js/apercu_fichier.js:a:titre_pdf");
        cadre.src = src;
        cadre.style.cssText = "display:block;width:100%;height:78vh;border:0;background:var(--voile-clair,#fff)";
        zone.appendChild(cadre);
      };
      const monterCadre = () => lirePdf(zone, src, jeton, cadreIframe);
      const pdf = () => fetch(src, {method: "HEAD", credentials: "same-origin"})   // service de conversion absent : message + « Ouvrir » (téléchargement)
        .then(r => { if (zone._jeton !== jeton) return; if (!r.ok) throw new Error(r.status); monterCadre(); })
        .catch(() => { if (zone._jeton === jeton) { zone.textContent = ""; const m = document.createElement("div"); m.style.cssText = "padding:12px"; m.textContent = tj("engine/static/engine/js/apercu_fichier.js:g:erreur"); zone.appendChild(m); } });
      if (!office) monterCadre();
      else if (!EXT_DIAPO.has(extension(url))) pdf();
      else {   // présentation : vidéo (durées et transitions des diapositives) ; conversion impossible → repli sur l'aperçu PDF
        const video = src.replace("apercu=pdf", "apercu=video");
        zone.textContent = tj("engine/static/engine/js/apercu_fichier.js:g:chargement");
        fetch(video, {method: "HEAD", credentials: "same-origin"}).then(r => {
          if (zone._jeton !== jeton || !r.ok) throw new Error(r.status);
          const v = Object.assign(document.createElement("video"), {src: video, controls: true}); zone.textContent = ""; zone.appendChild(v);
          v.style.cssText = "display:block;width:100%;max-height:78vh;background:#000";
        }).catch(() => { if (zone._jeton === jeton) { zone.textContent = ""; pdf(); } });
      }
    } else if (g === "texte") {
      const bloc = document.createElement("pre");
      bloc.style.cssText = "margin:0;padding:12px;width:100%;max-height:78vh;overflow:auto;box-sizing:border-box;white-space:pre-wrap;word-break:break-word;text-align:left;font:12px/1.5 ui-monospace,Menlo,Consolas,monospace;color:var(--text,inherit);background:var(--panel,transparent)";
      bloc.textContent = tj("engine/static/engine/js/apercu_fichier.js:g:chargement");
      zone.appendChild(bloc);
      fetch(url, {credentials: "same-origin"})
        .then(r => { if (!r.ok) throw new Error(r.status); return r.text(); })
        .then(texte => {
          if (zone._jeton !== jeton) return;
          bloc.textContent = texte;   // le fichier entier, jamais tronqué
        })
        .catch(() => { if (zone._jeton === jeton) bloc.textContent = tj("engine/static/engine/js/apercu_fichier.js:g:erreur"); });
    }
  }

  const echapper = v => String(v == null ? "" : v).replace(/[&<>"']/g, c => "&#" + c.charCodeAt(0) + ";");

  /* Bouton « Aperçu » d'une cellule fichier : même mécanisme que « ⤢ Ouvrir » des images/vidéos (classe .btn-ouvrir-media,
     écoutée par ouvrirVisionneuse). Chaîne vide si le fichier n'a pas d'aperçu intégré. */
  function bouton(url, titre){
    const g = genre(url);
    if (!g) return "";
    return `<button type="button" class="btn-ouvrir-media" data-type="${g}" data-url="${echapper(url)}" data-titre="${echapper(titre)}" title="${echapper(tj("engine/static/engine/js/apercu_fichier.js:a:bouton_titre"))}" style="color:var(--accent);background:transparent;text-decoration:none;font-size:12px;border:1px solid var(--border);border-radius:4px;padding:2px 7px;line-height:1.6;cursor:pointer;display:inline-flex;align-items:center;gap:3px;flex-shrink:0">${echapper(tj("engine/static/engine/js/apercu_fichier.js:g:bouton"))}</button>`;
  }

  window.ApercuFichier = {genre, monter, vider, bouton};
})();
