// Thème visuel des formulaires de saisie — UN seul moteur de rendu, partagé par l'éditeur visuel (index) et le formulaire client :
// l'aperçu de l'éditeur est donc identique à ce que voit le client. Aucune dépendance externe (polices système, images en data-URI).
// Le thème est stocké dans table.structure_formulaire.theme (voir engine/formulaire_conditionnel.py : normaliser_theme).
(function(){
  const POLICES = {
    systeme: 'system-ui,-apple-system,"Segoe UI",Roboto,sans-serif',
    moderne: '"Helvetica Neue",Helvetica,Arial,sans-serif',
    humaniste: '"Gill Sans","Trebuchet MS",Calibri,sans-serif',
    serif: 'Georgia,"Times New Roman",serif',
    elegante: '"Palatino Linotype",Palatino,"Book Antiqua",serif',
    arrondie: 'ui-rounded,"SF Pro Rounded","Varela Round",Nunito,"Trebuchet MS",sans-serif',
    manuscrite: '"Segoe Script","Bradley Hand","Comic Sans MS",cursive',
    affiche: 'Impact,"Arial Narrow Bold",Haettenschweiler,sans-serif',
    mono: 'ui-monospace,"SF Mono",Consolas,monospace',
  };
  const PRESETS = {
    flat:    {fond: "#2fb374", carte: "#ffffff", texte: "#111111", accent: "#35dc96"},
    verre:   {fond: "#4f46e5", carte: "#ffffff", texte: "#ffffff", accent: "#ffffff"},
    papier:  {fond: "#f1e6d0", carte: "#fffdf7", texte: "#3b2f20", accent: "#b5542c"},
    nuit:    {fond: "#0b1020", carte: "#141b34", texte: "#e7ecff", accent: "#6bcfff"},
    minimal: {fond: "#f2f2f2", carte: "#ffffff", texte: "#111111", accent: "#111111"},
  };
  const ROLES = ["titre", "description", "section", "question", "saisie"];
  // Mise en forme par défaut de chaque rôle (reprend le motif : libellés en petites capitales espacées, titre net).
  const STYLES_DEFAUT = {
    titre:       {police: "systeme", gras: true,  italique: false, souligne: false, taille: 26, align: "left"},
    description: {police: "systeme", gras: false, italique: false, souligne: false, taille: 14, align: "left"},
    section:     {police: "systeme", gras: true,  italique: false, souligne: false, taille: 18, align: "left"},
    question:    {police: "systeme", gras: true,  italique: false, souligne: false, taille: 11, align: "left"},
    saisie:      {police: "systeme", gras: false, italique: false, souligne: false, taille: 15, align: "left"},
  };

  const HEX = /^#[0-9a-f]{6}$/i;
  const rgb = (h) => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const hex = (v) => "#" + v.map(x => Math.max(0, Math.min(255, Math.round(x))).toString(16).padStart(2, "0")).join("");
  const assombrir = (h, f) => hex(rgb(h).map(x => x * f));
  const texteSur = (h) => { const v = rgb(h); return (0.299 * v[0] + 0.587 * v[1] + 0.114 * v[2]) > 150 ? "#111111" : "#ffffff"; };

  // Thème complet = style prédéfini + valeurs choisies (les valeurs absentes retombent sur le style, puis sur les défauts).
  function resoudre(t){
    t = t || {};
    const preset = PRESETS[t.preset] ? t.preset : "flat";
    const base = PRESETS[preset];
    const r = {preset};
    for(const k of ["fond", "carte", "texte", "accent"]) r[k] = HEX.test(t[k] || "") ? t[k] : base[k];
    r.image_garde = t.image_garde || ""; r.image_fond = t.image_fond || "";
    r.illustration = t.illustration !== false;
    r.garde_hauteur = Number.isFinite(+t.garde_hauteur) && t.garde_hauteur ? +t.garde_hauteur : 260;
    r.voile = Number.isFinite(+t.voile) && t.voile !== undefined ? +t.voile : 35;
    r.flou = +t.flou || 0;
    r.rayon = t.rayon === undefined ? 22 : +t.rayon;
    r.colonnes = +t.colonnes === 2 ? 2 : 1;
    r.sections = (t.sections && typeof t.sections === "object") ? t.sections : {};   // apparence par section (clé = id de section)
    r.champs = (t.champs && typeof t.champs === "object") ? t.champs : {};            // taille des polices par champ (clé = slug)
    r.styles = {};
    for(const role of ROLES) r.styles[role] = Object.assign({}, STYLES_DEFAUT[role], (t.styles && t.styles[role]) || {});
    return r;
  }

  // Skyline au trait (illustration par défaut de l'image de garde), dessinée à la couleur d'accent.
  function skyline(c){
    const s = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 110" fill="none" stroke="' + c + '" stroke-width="1.6" stroke-linejoin="round">'
      + '<path d="M0 108H320"/><rect x="20" y="44" width="34" height="64"/><rect x="62" y="30" width="26" height="78"/><path d="M96 108V10M102 108V4M108 108V12"/>'
      + '<rect x="124" y="52" width="30" height="56"/><rect x="158" y="40" width="22" height="68"/><path d="M164 40V30h10v10"/><rect x="184" y="52" width="30" height="56"/>'
      + '<rect x="232" y="26" width="30" height="82"/><path d="M130 66h6m6 0h6M130 80h6m6 0h6M130 94h6m6 0h6M240 40h6m6 0h6M240 56h6m6 0h6M240 72h6m6 0h6M240 88h6m6 0h6"/>'
      + '<path d="M270 108V84l12-10 12 10v24M110 108V80l10-8 10 8v28"/><circle cx="196" cy="14" r="8"/><path d="M196 0v4M196 24v4M182 14h4M206 14h4M186 4l3 3M203 21l3 3M206 4l-3 3M189 21l-3 3"/></svg>';
    return "url(\"data:image/svg+xml;utf8," + encodeURIComponent(s) + "\")";
  }

  // Applique le thème à une « scène » (zone du formulaire client, ou aperçu de l'éditeur). Idempotent : peut être rappelé à chaque changement.
  // La scène contient .form-titre, .form-desc (déplacés dans l'image de garde), puis le reste du formulaire (déplacé dans la carte).
  function appliquer(zone, theme){
    const t = resoudre(theme);
    zone.classList.add("gyf");
    zone.dataset.preset = t.preset;
    zone.dataset.col = String(t.colonnes);
    let carte = Array.from(zone.children).find(e => e.classList.contains("gyf-carte"));
    if(!carte){
      carte = document.createElement("div"); carte.className = "gyf-carte";
      while(zone.firstChild) carte.appendChild(zone.firstChild);
      zone.appendChild(carte);
    }
    let garde = carte.querySelector(":scope > .gyf-garde");
    if(!garde){
      garde = document.createElement("div"); garde.className = "gyf-garde";
      garde.innerHTML = '<div class="gyf-garde-visuel"></div><div class="gyf-garde-texte"></div>';
      const texte = garde.querySelector(".gyf-garde-texte");
      const titre = carte.querySelector(".form-titre"), desc = carte.querySelector(".form-desc");
      if(titre) texte.appendChild(titre);
      if(desc) texte.appendChild(desc);
      carte.insertBefore(garde, carte.firstChild);
    }
    const mode = t.image_garde ? "image" : (t.illustration ? "illustration" : "aucune");
    garde.className = "gyf-garde gyf-garde--" + mode;
    const visuel = garde.querySelector(".gyf-garde-visuel");
    visuel.style.backgroundImage = mode === "image" ? 'url("' + t.image_garde + '")' : (mode === "illustration" ? skyline(t.accent) : "none");
    const form = zone.querySelector("form"); if(form) form.classList.add("gyf-champs");

    const s = zone.style;
    s.setProperty("--gf-fond", t.fond); s.setProperty("--gf-carte", t.carte); s.setProperty("--gf-texte", t.texte);
    s.setProperty("--gf-accent", t.accent); s.setProperty("--gf-sur-accent", texteSur(t.accent));
    s.setProperty("--gf-ombre", assombrir(t.fond, 0.78));
    s.setProperty("--gf-rayon", t.rayon + "px");
    s.setProperty("--gf-garde-h", (mode === "image" ? t.garde_hauteur : Math.min(t.garde_hauteur, 150)) + "px");
    s.setProperty("--gf-voile", String(t.voile / 100));
    s.setProperty("--gf-flou", t.flou + "px");
    s.setProperty("--gf-img-fond", t.image_fond ? 'url("' + t.image_fond + '")' : "none");
    for(const role of ROLES){
      const st = t.styles[role];
      s.setProperty("--gf-" + role + "-police", POLICES[st.police] || POLICES.systeme);
      s.setProperty("--gf-" + role + "-gras", st.gras ? "800" : "400");
      s.setProperty("--gf-" + role + "-italique", st.italique ? "italic" : "normal");
      s.setProperty("--gf-" + role + "-soulignement", st.souligne ? "underline" : "none");
      s.setProperty("--gf-" + role + "-taille", st.taille + "px");
      s.setProperty("--gf-" + role + "-align", st.align);
      if(st.couleur) s.setProperty("--gf-" + role + "-couleur", st.couleur); else s.removeProperty("--gf-" + role + "-couleur");
    }
    appliquerDetails(zone, theme);
    return t;
  }

  // ── Apparence par section et par champ ─────────────────────────────────────────────────────────────────────────────
  // Les panneaux `.form-section[data-section-id]` (client, index, aperçu de l'éditeur) reçoivent leurs propres variables --gs-* ;
  // les blocs de champ (`.groupe[data-slug]` ou `.champ-item[data-champ-slug]`) reçoivent la taille de leurs polices (--gc-*).
  const VARS_SECTION = ["fond", "bordure", "rayon", "titre", "description", "question", "saisie", "image", "voile", "voile-couleur"];
  const ATTRS_SECTION = ["fond", "bordure", "titre", "description", "question", "saisie", "image"];
  const MAJ = (c) => c.replace(/(^|-)(\w)/g, (m, a, b) => b.toUpperCase());

  function appliquerSection(panneau, st){
    for(const v of VARS_SECTION) panneau.style.removeProperty("--gs-" + v);
    for(const a of ATTRS_SECTION) delete panneau.dataset["gs" + MAJ(a)];
    panneau.classList.remove("gys", "gys-boite");
    if(!st || typeof st !== "object") return;
    panneau.classList.add("gys");
    for(const k of ["fond", "bordure", "titre", "description", "question", "saisie"]){
      if(HEX.test(st[k] || "")){ panneau.style.setProperty("--gs-" + k, st[k]); panneau.dataset["gs" + MAJ(k)] = "1"; }
    }
    if(Number.isFinite(+st.rayon) && st.rayon !== undefined) panneau.style.setProperty("--gs-rayon", (+st.rayon) + "px");
    if(typeof st.image === "string" && /^data:image\//.test(st.image)){
      panneau.style.setProperty("--gs-image", 'url("' + st.image + '")');
      panneau.style.setProperty("--gs-voile", (Number.isFinite(+st.voile) ? +st.voile : 35) + "%");
      panneau.style.setProperty("--gs-voile-couleur", HEX.test(st.fond || "") ? st.fond : "#000000");
      panneau.dataset.gsImage = "1";
    }
    if(st.fond || st.bordure || st.image) panneau.classList.add("gys-boite");
  }

  function appliquerChamp(bloc, st){
    for(const v of ["question", "saisie"]) bloc.style.removeProperty("--gc-" + v);
    delete bloc.dataset.gcQ; delete bloc.dataset.gcS;
    if(!st || typeof st !== "object") return;
    if(Number.isFinite(+st.taille_question) && st.taille_question){ bloc.style.setProperty("--gc-question", (+st.taille_question) + "px"); bloc.dataset.gcQ = "1"; }
    if(Number.isFinite(+st.taille_saisie) && st.taille_saisie){ bloc.style.setProperty("--gc-saisie", (+st.taille_saisie) + "px"); bloc.dataset.gcS = "1"; }
  }

  // Idempotent : à rappeler après toute (re)construction des sections ou des champs.
  function appliquerDetails(zone, theme){
    const t = resoudre(theme);
    zone.querySelectorAll(".form-section").forEach(p => { appliquerSection(p, p.dataset.sectionId ? t.sections[p.dataset.sectionId] : null); });
    zone.querySelectorAll(".groupe[data-slug], .champ-item[data-champ-slug]").forEach(b => { appliquerChamp(b, t.champs[b.dataset.slug || b.dataset.champSlug]); });
    return t;
  }

  // Image choisie → data-URI redimensionnée et recompressée pour tenir sous `limite` caractères (plafond serveur).
  // Le navigateur est seul juge : on n'exige PAS de type MIME (vide ou inexact pour .jfif, .avif, .bmp, .svg… selon le système) —
  // si l'image se décode, elle est acceptée. WebP d'abord (garde la transparence), JPEG sur fond blanc en repli (Safari ancien) ;
  // si c'est encore trop lourd, la qualité puis les dimensions baissent. Erreurs : "type" (illisible) ou "taille" (irréductible).
  function imageVersDataUri(fichier, largeurMax, limite){
    return new Promise((ok, ko) => {
      if(!fichier) return ko(new Error("type"));
      // Type MIME absent ou inexact (fréquent sous Windows/Android) : on le déduit de l'extension, sinon un .svg n'est jamais reconnu comme image.
      const ext = ((fichier.name || "").split(".").pop() || "").toLowerCase();
      const MIME = {svg: "image/svg+xml", jpg: "image/jpeg", jpeg: "image/jpeg", jfif: "image/jpeg", pjpeg: "image/jpeg", png: "image/png", webp: "image/webp", gif: "image/gif", bmp: "image/bmp", avif: "image/avif", ico: "image/x-icon"};
      const blob = (/^image\//.test(fichier.type) || !MIME[ext]) ? fichier : new Blob([fichier], {type: MIME[ext]});
      const url = URL.createObjectURL(blob), img = new Image();
      img.onerror = () => { URL.revokeObjectURL(url); ko(new Error("type")); };
      img.onload = () => {
        URL.revokeObjectURL(url);
        const L = img.naturalWidth || 1200, H = img.naturalHeight || Math.round(L * 0.66);   // SVG sans dimensions propres : taille par défaut
        const poser = (c, fondBlanc) => { const g = c.getContext("2d"); if(fondBlanc){ g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height); } g.drawImage(img, 0, 0, c.width, c.height); return c; };
        for(const echelle of [1, 0.75, 0.55, 0.4]){
          const k = Math.min(1, largeurMax / L) * echelle;
          const c = document.createElement("canvas"); c.width = Math.max(1, Math.round(L * k)); c.height = Math.max(1, Math.round(H * k));
          for(const [mime, blanc] of [["image/webp", false], ["image/jpeg", true]]){
            poser(c, blanc);
            for(let q = 0.86; q >= 0.3; q -= 0.14){
              let d; try{ d = c.toDataURL(mime, q); }catch(e){ return ko(new Error("type")); }   // canvas « teinté » (image inaccessible) : illisible
              if(!d.startsWith("data:" + mime)) break;                                           // format non encodable par ce navigateur : suivant
              if(d.length <= limite) return ok(d);
            }
          }
        }
        ko(new Error("taille"));
      };
      img.src = url;
    });
  }

  window.gyFormTheme = {POLICES, PRESETS, ROLES, STYLES_DEFAUT, resoudre, appliquer, appliquerDetails, imageVersDataUri};
})();
