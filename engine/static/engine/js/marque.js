// ── Personnalisation de la marque (marque blanche) ───────────────────────────────────────
// Lue sur GET /api/marque/ (engine/marque.py) et posée sur toutes les pages : logo (barre latérale, intro, connexion), nom
// (intro, barre latérale, titre) et slogans de l'intro. Une valeur absente laisse la page telle quelle (identité d'origine).
// Icône « Modules » (sans texte) dans TOUS les en-têtes, juste avant le logo : ☰ · Modules · logo + nom · titre. Le libellé (infobulle et
// lecteurs d'écran) et la présence de l'icône viennent du serveur (/api/marque/ → modules) : absente hors connexion, et chez le client
// seulement s'il a des pages d'analyse ouvertes (la page qu'elle ouvre ne lui liste alors que celles-là).
// Les couleurs, elles, arrivent par /marque.css (variables CSS), sans script. Aucun texte d'interface n'est écrit ici.
(function(){
  const SRC = document.currentScript && document.currentScript.src;
  // Tant que le logo de l'entreprise n'est pas posé, /marque.css masque les logos d'origine : on lève ce masque dans tous les cas
  // (logo posé, logo illisible, pas de logo, réseau en panne) pour qu'aucun logo ne reste invisible.
  const prete = () => document.documentElement.classList.add("marque-prete");
  setTimeout(prete, 3000);
  // Adresse de la page Modules : celle de la table de la page (/tables/<id>/…), sinon /modules/ qui retrouve la table courante
  // (table de la page d'analyse, ou dernière table ouverte à l'accueil, mémorisée par index_1.js) côté serveur, qui contrôle les droits.
  function lienModules(){
    let m = location.pathname.match(/^\/tables\/(\d+)\//);
    if(m) return "/tables/" + m[1] + "/modules/";
    let indice = "";
    m = location.pathname.match(/^\/tables\/([^\/]+)\//);
    if(m) indice = m[1];
    else if(window.ANALYSE && window.ANALYSE.tableId) indice = String(window.ANALYSE.tableId);
    else { try { const c = JSON.parse(localStorage.getItem("gy_accueil_contexte") || "null"); if(c && typeof c.slug === "string") indice = c.slug; } catch(e){ /* stockage indisponible */ } }
    return "/modules/" + (indice ? "?table=" + encodeURIComponent(indice) : "");
  }
  function boutonModules(d){
    if(!d || !d.modules || !d.modules.libelle || document.getElementById("btn-modules-entete")) return null;
    if(!document.getElementById("gy-modules-css")){
      const st = document.createElement("style");
      st.id = "gy-modules-css";
      st.textContent = "#btn-modules-entete{display:inline-flex;align-items:center;justify-content:center;flex:none;align-self:center;box-sizing:border-box;width:34px;height:30px;padding:0;background:transparent;border:1px solid var(--border,#2a2a2a);border-radius:6px;color:var(--text,inherit);text-decoration:none;cursor:pointer}"
        + "#btn-modules-entete:hover,#btn-modules-entete:focus-visible{border-color:var(--accent,#6bcfff);color:var(--accent,#6bcfff)}"
        + "#btn-modules-entete svg{display:block;width:18px;height:18px;fill:currentColor}"
        + ".gy-bande-seule{display:flex;align-items:center;gap:10px;padding:6px 14px;background:var(--panel,#0a0a0a);border-bottom:1px solid var(--border,#2a2a2a);position:relative;z-index:5}"
        + "body.plein-ecran .gy-bande-seule{display:none}";
      document.head.appendChild(st);
    }
    const a = document.createElement("a");
    a.id = "btn-modules-entete"; a.href = lienModules();
    a.title = d.modules.libelle; a.setAttribute("aria-label", d.modules.libelle);
    a.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/></svg>';
    return a;
  }
  // Pose l'icône juste avant le logo, dans l'en-tête de la page : bandeau fusionné (#entete), bandeau simple (.header), ou bande de
  // marque seule. Sans logo dans l'en-tête, elle prend la place qui lui revient : juste après ☰, sinon en tête.
  function poserModules(d){
    const mod = boutonModules(d);
    if(!mod) return;
    const marque = document.getElementById("gy-marque") || document.getElementById("marque");
    if(marque && marque.classList.contains("seule")){   // bande de marque seule : un conteneur commun, une balise <a> ne pouvant en contenir une autre
      const bande = document.createElement("div");
      bande.className = "gy-bande-seule";
      marque.parentNode.insertBefore(bande, marque);
      marque.classList.remove("seule");
      bande.appendChild(mod); bande.appendChild(marque);
      return;
    }
    if(marque && marque.parentNode && marque.closest("#entete, .header, .profil-header")){ marque.parentNode.insertBefore(mod, marque); return; }
    const cont = document.querySelector("#entete .entete-gauche, .header, .profil-header");
    if(!cont) return;
    const bascule = cont.querySelector("#btn-colonne, #btn-toggle-sidebar");
    cont.insertBefore(mod, bascule && bascule.parentNode === cont ? bascule.nextSibling : cont.firstChild);
  }
  // Marque (logo + nom, lien vers la page principale) sur TOUTES les pages : fondue dans le bandeau existant (.header des pages simples ;
  // #entete des pages à barre latérale, voir fusionnerBandeau), ou en bande seule quand la page n'a aucun bandeau. Les pages qui
  // montrent déjà leur marque (connexion, inscription, intro, espace client, coque) sont laissées telles quelles.
  function bandeMarque(d){
    const b = document.body;
    if(!b || document.getElementById("gy-marque") || /\bpage-(client|coque|login|inscription|login_verification)\b/.test(b.className)) return;
    if(document.querySelector("#marque, #entete-titre, .login-logo, .intro-logo, img.logo-gy")) return;
    const nom = d.nom || d.defaut_nom || "";
    const logo = d.logo_defaut || (SRC ? SRC.replace(/js\/[^\/]*$/, "img/logo_agent_gy.png") : "");   // logo d'origine ; celui de l'entreprise le remplace plus bas, une fois chargé (comme sur les autres pages)
    const st = document.createElement("style");
    st.textContent = "#gy-marque{display:flex;align-items:center;gap:10px;text-decoration:none;flex:none;min-width:0}"
      + "#gy-marque.seule{padding:6px 14px;background:var(--panel,#0a0a0a);border-bottom:1px solid var(--border,#2a2a2a);position:relative;z-index:5}"
      + "#gy-marque img{width:26px;height:26px;object-fit:contain;flex-shrink:0}"
      + "#gy-marque span{font-weight:700;font-size:14px;color:var(--nom-app,var(--accent,#6bcfff));white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:40vw}"
      + "body.plein-ecran #gy-marque.seule{display:none}";
    document.head.appendChild(st);
    const a = document.createElement("a");
    a.id = "gy-marque"; a.href = "/"; a.title = nom;
    if(logo){ const i = document.createElement("img"); i.className = "logo-gy"; i.alt = nom; i.src = logo; i.onerror = () => i.remove(); a.appendChild(i); }
    const sp = document.createElement("span"); sp.textContent = nom; a.appendChild(sp);
    const h = document.querySelector(".header, .profil-header");
    if(h && !h.closest("#app")){
      // Ordre unique du bandeau sur toutes les pages : ☰ (s'il existe) · marque (logo + nom) · titre de la page.
      const bascule = Array.from(h.children).find(c => c.matches("#btn-colonne, #btn-toggle-sidebar"));
      h.insertBefore(a, bascule ? bascule.nextSibling : h.firstChild);
    }
    else { a.className = "seule"; b.insertBefore(a, b.firstChild); }
  }
  // Bandeau unique des pages à barre latérale : l'entête (#entete) est remonté en pleine largeur au-dessus de #app et absorbe la
  // marque (#marque, ex-bloc logo + nom en haut de la barre latérale) ; le bouton ☰ n'existe que là où il y a une barre latérale.
  // Les nœuds sont déplacés (jamais copiés) : identifiants, écouteurs et scripts des pages continuent de fonctionner.
  function fusionnerBandeau(){
    const b = document.body, ent = document.getElementById("entete"), app = document.getElementById("app");
    if(!b || !ent || !app || b.classList.contains("gy-bande-fusionnee")) return;
    let marque = document.getElementById("marque");
    if(marque && marque.tagName !== "A"){   // logo + nom = lien vers la page principale
      const a = document.createElement("a");
      a.id = "marque"; a.href = "/"; a.title = marque.textContent.trim(); a.className = marque.className;
      while(marque.firstChild) a.appendChild(marque.firstChild);
      marque.replaceWith(a); marque = a;
    }
    const gauche = ent.querySelector(".entete-gauche"), bascule = document.getElementById("btn-toggle-sidebar");
    if(gauche){
      if(bascule) gauche.insertBefore(bascule, gauche.firstChild);
      if(marque) gauche.insertBefore(marque, bascule ? bascule.nextSibling : gauche.firstChild);
    } else if(marque) marque.remove();
    app.parentNode.insertBefore(ent, app);
    b.classList.add("gy-bande-fusionnee");
    const mesurer = () => document.body.style.setProperty("--gy-entete-h", Math.ceil(ent.getBoundingClientRect().bottom + window.scrollY) + "px");
    mesurer();
    if(window.ResizeObserver) new ResizeObserver(mesurer).observe(ent); else window.addEventListener("resize", mesurer);
  }
  fusionnerBandeau();
  function appliquer(d){
    bandeMarque(d);
    poserModules(d);   // avant la levée du masque des logos (prete) : l'en-tête est complet d'un coup
    if(d.nom){
      window.gyNomApp = d.nom;   // nom choisi : réutilisé par la page index (vue sans table) pour le titre de l'entête
      const titre = document.getElementById("entete-titre");
      if(titre && (titre.textContent.trim() === (d.defaut_nom || "").trim() || titre.textContent.trim() === (window.tj ? window.tj("engine/static/engine/js/marque.js:nom_application") : ""))) titre.textContent = d.nom;
      document.querySelectorAll("#marque, .intro-logo, .login-logo h1").forEach(el => {
        for(const n of el.childNodes){
          if(n.nodeType === 3 && n.nodeValue.trim()){ n.nodeValue = (el.id === "marque" ? " " : "") + d.nom; break; }
        }
      });
      document.querySelectorAll(".login-logo img").forEach(i => { i.alt = d.nom; });
      if(d.defaut_nom) document.title = document.title.split(d.defaut_nom).join(d.nom);
    }
    [[".intro-sub", d.slogan], [".intro-tagline", d.slogan2]].forEach(([sel, v]) => {
      if(typeof v !== "string") return;
      document.querySelectorAll(sel).forEach(el => { el.textContent = v; el.style.display = v ? "" : "none"; });
    });
    if(d.logo_url){   // le logo n'est posé qu'une fois chargé : jamais d'image cassée à la place de celui d'origine
      const test = new Image();
      test.onload = () => {
        const imgs = Array.from(document.querySelectorAll("img.logo-gy, img.intro-logo-img, .login-logo img"));
        imgs.forEach(i => { i.src = d.logo_url; });
        Promise.all(imgs.map(i => i.decode ? i.decode().catch(() => {}) : null)).then(prete);
      };
      test.onerror = prete;
      test.src = d.logo_url;
    } else { prete(); }
  }
  fetch("/api/marque/", {credentials: "same-origin"}).then(r => r.ok ? r.json() : null).then(d => { if(d) appliquer(d); else prete(); }).catch(prete);
})();
