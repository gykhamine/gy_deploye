/* Page SIEM : tout le texte venant du serveur (noms de comptes, messages d'alerte, chemins) est écrit avec textContent, jamais innerHTML —
   ces champs peuvent contenir ce qu'un attaquant a tapé. */
(function () {
  "use strict";
  var racine = document.getElementById("sm-app");
  var bandeaux = document.getElementById("sm-bandeaux");
  var fenetreReauth = document.getElementById("sm-reauth");
  var enAttente = [];
  var minuterie = null;

  function jeton() {
    var m = document.cookie.match(/(?:^|; )gy_csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function el(nom, attrs, enfants) {
    var n = document.createElement(nom);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "texte") n.textContent = attrs[k];
      else if (k === "classe") n.className = attrs[k];
      else if (k.indexOf("on") === 0) n.addEventListener(k.slice(2), attrs[k]);
      else n.setAttribute(k, attrs[k]);
    });
    (enfants || []).forEach(function (e) { n.appendChild(typeof e === "string" ? document.createTextNode(e) : e); });
    return n;
  }

  function api(url, corps) {
    var opts = corps === undefined ? { credentials: "same-origin" } : {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": jeton() }, body: JSON.stringify(corps)
    };
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.status === 403 && j.reauth) {
          return new Promise(function (ok, ko) { enAttente.push({ url: url, corps: corps, ok: ok, ko: ko }); demanderReauth(); });
        }
        if (!r.ok) throw new Error(j.erreur || tj("engine/static/engine/js/siem.js:38:52", { v1: r.status }));
        return j;
      });
    });
  }

  function demanderReauth() {
    fenetreReauth.style.display = "flex";
    document.getElementById("sm-mdp").focus();
  }

  document.getElementById("sm-reauth-ok").addEventListener("click", function () {
    var champ = document.getElementById("sm-mdp");
    var msg = document.getElementById("sm-reauth-msg");
    msg.textContent = "";
    fetch("/api/siem/reauth", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": jeton() }, body: JSON.stringify({ mot_de_passe: champ.value })
    }).then(function (r) { return r.json().then(function (j) { return { r: r, j: j }; }); }).then(function (x) {
      if (!x.r.ok) { msg.textContent = x.j.erreur || tj("engine/static/engine/js/siem.js:57:58"); return; }
      champ.value = "";
      fenetreReauth.style.display = "none";
      var file = enAttente; enAttente = [];
      file.forEach(function (t) { api(t.url, t.corps).then(t.ok, t.ko); });
    });
  });
  document.getElementById("sm-mdp").addEventListener("keydown", function (e) {
    if (e.key === "Enter") document.getElementById("sm-reauth-ok").click();
  });

  function date(ts) { return ts ? new Date(ts * 1000).toLocaleString() : "—"; }
  var ETAT = { "sm-ok": "ok", "sm-ko": "ko", "sm-av": "warn" };
  var ouverts = {};   // sections repliables : leur état ouvert/fermé survit au rafraîchissement automatique

  function carte(titre, valeur, classe) {
    return el("div", { classe: "dc-carte " + (ETAT[classe] || "") }, [el("span", { classe: "dc-etiq", texte: titre }), el("span", { classe: "dc-val", texte: String(valeur) })]);
  }
  function chiffre(titre, valeur, classe) {
    return el("div", { classe: "chiffre " + (ETAT[classe] || "") }, [el("b", { texte: String(valeur) }), el("span", { texte: titre })]);
  }
  function bandeau(texte, alerte) {
    bandeaux.appendChild(el("div", { classe: "dc-bande" + (alerte ? " alerte" : ""), texte: texte }));
  }
  function bouton(texte, action, variante) { return el("button", { type: "button", classe: variante || "", texte: texte, onclick: action }); }

  function tableau(colonnes, lignes) {
    var t = el("table", { classe: "dc-table" });
    t.appendChild(el("thead", {}, [el("tr", {}, colonnes.map(function (c) { return el("th", { texte: c }); }))]));
    var corps = el("tbody");
    lignes.forEach(function (l) {
      corps.appendChild(el("tr", {}, l.map(function (c) { return (c instanceof Node) ? el("td", {}, [c]) : el("td", { texte: String(c == null ? "" : c) }); })));
    });
    if (!lignes.length) corps.appendChild(el("tr", {}, [el("td", { texte: tj("engine/static/engine/js/siem.js:84:79"), colspan: colonnes.length })]));
    t.appendChild(corps);
    return el("div", { classe: "dc-defil" }, [t]);
  }

  // Section repliable : le titre, un compteur facultatif, puis le contenu.
  function section(titre, contenu, ouvertParDefaut, compte) {
    var d = el("details", { classe: "dc-detail" });
    var resume = el("summary", {}, [el("span", { texte: titre })]);
    if (compte != null) resume.appendChild(el("span", { classe: "dc-compte", texte: String(compte) }));
    d.appendChild(resume);
    d.appendChild(el("div", { classe: "dc-corps" }, [].concat(contenu)));
    if (ouverts[titre] === undefined ? ouvertParDefaut : ouverts[titre]) d.setAttribute("open", "");
    d.addEventListener("toggle", function () { ouverts[titre] = d.open; });
    return d;
  }

  // Dialogues de la plateforme (dialogues.js) : jamais confirm()/alert() du navigateur.
  function confirmer(message, danger) { return window.GY.confirmer(message, danger ? { danger: true } : undefined); }
  function alerter(message) { return window.GY.alerter(message); }

  // ── Recherche (mêmes critères que la page blockchain) et lots masqués ──
  var CLES = ["q", "types", "champ", "valeur", "bloc_min", "bloc_max", "du", "au"];
  var params = new URLSearchParams(window.location.search);
  var avecMasques = !!params.get("avec_masques");
  function criteresActuels() {
    var c = {};
    CLES.forEach(function (k) {
      if (k === "types") { var t = params.getAll("types").filter(Boolean); if (t.length) c.types = t; }
      else if ((params.get(k) || "").trim()) c[k] = params.get(k).trim();
    });
    return c;
  }
  var rechActive = Object.keys(criteresActuels()).length > 0;
  function chaineRecherche() {
    var p = new URLSearchParams();
    CLES.forEach(function (k) { params.getAll(k).forEach(function (v) { if (v !== "") p.append(k, v); }); });
    if (avecMasques) p.set("avec_masques", "1");
    return p.toString();
  }
  function resumeCriteres(c) {
    return Object.keys(c).map(function (k) { return k + "=" + (Array.isArray(c[k]) ? c[k].join(",") : c[k]); }).join(" · ") || tj("engine/static/engine/js/siem.js:lot_tout");
  }

  function masquerLot() {
    var c = criteresActuels();
    return confirmer(tj("engine/static/engine/js/siem.js:masquer_confirmation", { v1: resumeCriteres(c) })).then(function (ok) {
      if (!ok) return;
      return api("/api/siem/masquer", { criteres: c }).then(function (r) {
        charger();
        return alerter(tj("engine/static/engine/js/siem.js:masquer_ok", { v1: r.n }));
      }).catch(function (e) { return alerter(e.message); });
    });
  }
  function reafficherLot(id) {
    var msg = id === "tout" ? tj("engine/static/engine/js/siem.js:reafficher_tout_confirmation") : tj("engine/static/engine/js/siem.js:reafficher_confirmation");
    return agir("/api/siem/reafficher", { lot: id }, msg);
  }
  function barreLots(lots) {
    var b = [bouton(tj("engine/static/engine/js/siem.js:masquer_bouton"), masquerLot, "danger")];
    if (lots.length) b.push(bouton(tj("engine/static/engine/js/siem.js:reafficher_tout"), function () { reafficherLot("tout"); }));
    return el("div", { classe: "barre-actions" }, b);
  }
  function dessinerLots(lots) {
    var lignes = lots.map(function (l) {
      return [l.nom || tj("engine/static/engine/js/siem.js:lot_tout"), date(l.jusqua), date(l.ts), l.par || "",
        bouton(tj("engine/static/engine/js/siem.js:reafficher_bouton"), function () { reafficherLot(l.id); }, "petit")];
    });
    racine.appendChild(section(tj("engine/static/engine/js/siem.js:lots_titre"), [barreLots(lots), tableau([tj("engine/static/engine/js/siem.js:lots_col_criteres"),
      tj("engine/static/engine/js/siem.js:lots_col_jusqua"), tj("engine/static/engine/js/siem.js:lots_col_masque"), tj("engine/static/engine/js/siem.js:lots_col_par"), ""], lignes)],
      lots.length > 0, lots.length));
  }

  // ── Surlignage des mots cherchés et navigation ▲ / ▼ (comme la page blockchain) ──
  var plier = function (t) { return t.split("").map(function (c) { return c.normalize("NFD")[0].toLowerCase(); }).join(""); };   // même longueur : index alignés
  var marques = [], courant = -1, premiereRecherche = true;
  function surligner() {
    marques = [];
    var mots = (params.get("q") || "").split(/\s+/).filter(Boolean).map(plier);
    if (!mots.length) return;
    var noeuds = [], arbre = document.createTreeWalker(racine, NodeFilter.SHOW_TEXT);
    while (arbre.nextNode()) if (arbre.currentNode.nodeValue.trim() && !arbre.currentNode.parentElement.closest("script,style,summary,button,input,select")) noeuds.push(arbre.currentNode);
    noeuds.forEach(function (n) {
      var texte = n.nodeValue, pli = plier(texte), plages = [];
      mots.forEach(function (m) { for (var i = pli.indexOf(m); i >= 0; i = pli.indexOf(m, i + m.length)) plages.push([i, i + m.length]); });
      if (!plages.length) return;
      plages.sort(function (a, b) { return a[0] - b[0]; });
      var fusion = plages.reduce(function (r, p) { var d = r[r.length - 1]; if (d && p[0] <= d[1]) d[1] = Math.max(d[1], p[1]); else r.push(p.slice()); return r; }, []);
      var frag = document.createDocumentFragment(), pos = 0;
      fusion.forEach(function (ab) {
        frag.appendChild(document.createTextNode(texte.slice(pos, ab[0])));
        var m = document.createElement("mark"); m.className = "rech-mark"; m.textContent = texte.slice(ab[0], ab[1]); frag.appendChild(m); marques.push(m); pos = ab[1];
      });
      frag.appendChild(document.createTextNode(texte.slice(pos)));
      n.parentNode.replaceChild(frag, n);
    });
  }
  function afficherCompteur() {
    var nav = document.querySelector(".rech-nav"), cpt = document.querySelector(".rech-compteur");
    if (nav) nav.hidden = !marques.length;
    if (cpt) cpt.textContent = courant >= 0 ? (courant + 1) + " / " + marques.length : "";
  }
  function aller(pas, defiler) {
    if (!marques.length) return;
    if (courant >= 0 && marques[courant]) marques[courant].classList.remove("courant");
    courant = pas === 0 ? Math.min(courant, marques.length - 1) : (courant + pas + marques.length) % marques.length;
    var m = marques[courant]; m.classList.add("courant");
    if (defiler) {   // une correspondance dans une section repliée : on la déplie
      for (var d = m.closest("details"); d; d = d.parentElement && d.parentElement.closest("details")) d.open = true;
      m.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    afficherCompteur();
  }
  function appliquerRecherche() {
    surligner();
    if (premiereRecherche && marques.length) { courant = -1; aller(1, true); premiereRecherche = false; }
    else if (courant >= 0 && marques.length) aller(0, false);   // rafraîchissement automatique : on garde la correspondance courante, sans défiler
    else { courant = -1; afficherCompteur(); }
  }
  function brancherFormulaire() {
    var f = document.getElementById("sm-rech-form"); if (!f) return;
    ["q", "champ", "valeur", "bloc_min", "bloc_max", "du", "au"].forEach(function (k) { if (f.elements[k]) f.elements[k].value = params.get(k) || ""; });
    if (f.elements.avec_masques) f.elements.avec_masques.checked = avecMasques;
    var champ = document.getElementById("sm-rech-q");
    if (champ) {
      champ.setAttribute("data-applique", params.get("q") || "");
      // Entrée : correspondance suivante si la recherche affichée est déjà celle du champ ; sinon on lance la recherche
      champ.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" && marques.length && champ.value.trim() === (champ.getAttribute("data-applique") || "").trim()) { ev.preventDefault(); aller(ev.shiftKey ? -1 : 1, true); }
      });
    }
    var nav = document.querySelector(".rech-nav");
    if (nav) nav.addEventListener("click", function (ev) { var b = ev.target.closest("button[data-nav]"); if (b) aller(+b.getAttribute("data-nav"), true); });
    var bm = document.getElementById("sm-rech-masquer"); if (bm) bm.addEventListener("click", masquerLot);
    var det = document.getElementById("sm-rech"); if (det && (rechActive || avecMasques)) det.open = true;
  }
  function remplirTypes(types) {
    var sel = document.getElementById("sm-rech-types"); if (!sel) return;
    var choisis = params.getAll("types"), tous = types.slice();
    choisis.forEach(function (t) { if (tous.indexOf(t) < 0) tous.push(t); });
    sel.textContent = "";
    tous.sort().forEach(function (t) { var o = document.createElement("option"); o.value = t; o.textContent = t; o.selected = choisis.indexOf(t) >= 0; sel.appendChild(o); });
  }
  function resumeRecherche(nb, caches) {
    var r = document.getElementById("sm-rech-resume"); if (!r) return;
    r.textContent = (rechActive || caches) ? " — " + tj("engine/static/engine/js/siem.js:rech_resultat", { v1: nb, v2: caches }) : "";
  }

  function agir(url, corps, confirmation, danger) {
    return (confirmation ? confirmer(confirmation, danger) : Promise.resolve(true)).then(function (ok) {
      if (!ok) return;
      return api(url, corps || {}).then(function () { charger(); }).catch(function (e) { return alerter(e.message); });
    });
  }

  var dernierParquet = null;

  function octets(n) {
    if (n >= 1073741824) return (n / 1073741824).toFixed(1) + " " + tj("engine/static/engine/js/siem.js:97:73");
    if (n >= 1048576) return (n / 1048576).toFixed(1) + " " + tj("engine/static/engine/js/siem.js:98:67");
    return Math.max(1, Math.round(n / 1024)) + " " + tj("engine/static/engine/js/siem.js:99:58");
  }

  function etatParquet(k) {
    var noms = { ok: tj("engine/static/engine/js/siem.js:103:26"), reparee: tj("engine/static/engine/js/siem.js:103:52"), divergente: tj("engine/static/engine/js/siem.js:103:86"), absent: tj("engine/static/engine/js/siem.js:103:112"), ignoree: tj("engine/static/engine/js/siem.js:103:142"), erreur: tj("engine/static/engine/js/siem.js:103:172") };
    return noms[k] || k;
  }

  function verifierParquet(reparer, apres) {
    return (reparer ? confirmer(tj("engine/static/engine/js/siem.js:108:40")) : Promise.resolve(true)).then(function (ok) {
      if (!ok) return;
      return api("/api/siem/sante/parquet", { reparer: reparer, apres: apres || 0 })
        .then(function (r) { dernierParquet = r; charger(); })
        .catch(function (e) { return alerter(e.message); });
    });
  }

  function bandeauxSante(sante) {
    if (!sante) return;
    if (sante.base && !sante.base.ok) bandeau(tj("engine/static/engine/js/siem.js:116:51", { v1: sante.base.erreur || tj("engine/static/engine/js/siem.js:116:95") }), true);
    if (Array.isArray(sante.caches)) sante.caches.forEach(function (c) {
      if (!c.ok) bandeau(c.erreur ? tj("engine/static/engine/js/siem.js:118:41", { v1: c.nom, v2: c.erreur }) : tj("engine/static/engine/js/siem.js:118:94", { v1: c.nom }), true);
    });
    if (sante.redis && sante.redis.configure && !sante.redis.ok) bandeau(tj("engine/static/engine/js/siem.js:120:78", { v1: sante.redis.hote || "?" }), true);
  }

  function dessinerSante(sante) {
    var titreSante = tj("engine/static/engine/js/siem.js:124:46"), contenuSante = [];
    if (!sante) { racine.appendChild(section(titreSante, [el("div", { classe: "dc-av", texte: tj("engine/static/engine/js/siem.js:125:78") })], true)); return; }
    var b = sante.base || {}, rd = sante.redis || {}, pq = sante.parquet || {}, mr = sante.memoire_recherche || {}, er = sante.erreurs || {};
    var cartes = [carte(tj("engine/static/engine/js/siem.js:127:29"), b.ok ? ((b.moteur || "") + " · " + b.ms + " ms") : tj("engine/static/engine/js/siem.js:127:98", { v1: b.erreur || "" }), b.ok ? "sm-ok" : "sm-ko")];
    if (Array.isArray(sante.caches)) {
      sante.caches.forEach(function (c) {
        var plein = c.entrees != null && c.maximum && c.entrees / c.maximum > 0.9;
        var entrees = c.entrees == null ? "" : " · " + (c.maximum ? tj("engine/static/engine/js/siem.js:131:73", { v1: c.entrees, v2: c.maximum }) : tj("engine/static/engine/js/siem.js:131:127", { v1: c.entrees }));
        cartes.push(carte(tj("engine/static/engine/js/siem.js:132:31", { v1: c.nom }), c.ok ? (c.ms + " ms" + entrees) : tj("engine/static/engine/js/siem.js:132:99", { v1: c.erreur || "" }),
          !c.ok ? "sm-ko" : plein ? "sm-av" : "sm-ok"));
      });
    } else { cartes.push(carte(tj("engine/static/engine/js/siem.js:135:36"), tj("engine/static/engine/js/siem.js:135:56", { v1: (sante.caches || {}).erreur || "" }), "sm-ko")); }
    cartes.push(rd.configure === false ? carte("Redis (WebSocket)", tj("engine/static/engine/js/siem.js:136:73")) :
      carte("Redis (WebSocket)", rd.ok ? ((rd.hote || "") + " · " + rd.ms + " ms") : tj("engine/static/engine/js/siem.js:137:90", { v1: rd.erreur || "" }), rd.ok ? "sm-ok" : "sm-ko"));
    cartes.push(pq.erreur ? carte(tj("engine/static/engine/js/siem.js:138:39"), tj("engine/static/engine/js/siem.js:138:55", { v1: pq.erreur }), "sm-ko") :
      carte(tj("engine/static/engine/js/siem.js:139:17"), tj("engine/static/engine/js/siem.js:139:33", { v1: pq.fichiers, v2: pq.deltas, v3: octets(pq.octets || 0), v4: pq.tables_eav }) +
        (pq.pyarrow ? "" : tj("engine/static/engine/js/siem.js:140:32")) + (pq.en_pause ? tj("engine/static/engine/js/siem.js:140:71") : ""), pq.pyarrow && !pq.en_pause ? "sm-ok" : "sm-av"));
    cartes.push(mr.erreur ? carte(tj("engine/static/engine/js/siem.js:141:39"), tj("engine/static/engine/js/siem.js:141:56", { v1: mr.erreur }), "sm-ko") :
      carte(tj("engine/static/engine/js/siem.js:142:17"), tj("engine/static/engine/js/siem.js:142:34", { v1: mr.tables, v2: mr.lignes, v3: mr.maximum }) + (mr.recherche_parquet ? "" : tj("engine/static/engine/js/siem.js:142:133")), mr.recherche_parquet ? "" : "sm-av"));
    cartes.push(er.erreur ? carte(tj("engine/static/engine/js/siem.js:143:39"), tj("engine/static/engine/js/siem.js:143:56", { v1: er.erreur }), "sm-ko") : carte(tj("engine/static/engine/js/siem.js:143:114"), er.total_24h, er.total_24h ? "sm-av" : "sm-ok"));
    contenuSante.push(el("div", { classe: "dc-cartes" }, cartes));

    contenuSante.push(el("div", { classe: "barre-actions" }, [
      bouton(tj("engine/static/engine/js/siem.js:147:18"), function () { verifierParquet(false, 0); }),
      bouton(tj("engine/static/engine/js/siem.js:148:18"), function () { verifierParquet(true, 0); })
    ]));
    if (dernierParquet) {
      var r = dernierParquet;
      var etats = Object.keys(r.etats).map(function (k) { return etatParquet(k) + " " + r.etats[k]; }).join(" · ") || tj("engine/static/engine/js/siem.js:152:123");
      contenuSante.push(el("p", { classe: "aide", texte: (r.reparer ? tj("engine/static/engine/js/siem.js:153:62", { v1: r.verifiees, v2: etats }) : tj("engine/static/engine/js/siem.js:153:117", { v1: r.verifiees, v2: etats })) + (r.tronque ? tj("engine/static/engine/js/siem.js:153:182") : "") }));
      if (r.tronque) contenuSante.push(el("div", { classe: "barre-actions" }, [bouton(tj("engine/static/engine/js/siem.js:154:87"), function () { verifierParquet(r.reparer, r.suite); })]));
      contenuSante.push(tableau([tj("engine/static/engine/js/siem.js:155:39"), tj("engine/static/engine/js/siem.js:155:59"), tj("engine/static/engine/js/siem.js:155:78"), tj("engine/static/engine/js/siem.js:155:100"), tj("engine/static/engine/js/siem.js:155:122")], r.ecarts.map(function (x) {
        return [x.nom, etatParquet(x.etat), x.lignes_eav, x.lignes_fichier, (x.colonnes || []).join(", ")];
      })));
    }
    if (er.recentes) {
      contenuSante.push(el("h3", { texte: tj("engine/static/engine/js/siem.js:160:48") + (er.fichier ? " (" + er.fichier + ")" : "") }));
      contenuSante.push(tableau([tj("engine/static/engine/js/siem.js:161:39"), tj("engine/static/engine/js/siem.js:161:58"), tj("engine/static/engine/js/siem.js:161:79"), tj("engine/static/engine/js/siem.js:161:100")], er.recentes.map(function (x) { return [x.date, x.niveau, x.source, x.message]; })));
    }
    racine.appendChild(section(titreSante, contenuSante, true));
  }

  function dessiner(etat, alertes, risques, evenements, sante, fantome) {
    bandeaux.textContent = "";
    if (etat.lockdown) bandeau(tj("engine/static/engine/js/siem.js:167:36"), true);
    if (etat.mode === "observation") bandeau(tj("engine/static/engine/js/siem.js:168:50", { v1: etat.jours_observation, v2: etat.observation_requise_jours }));
    if (!etat.chaine_systeme.valide) bandeau(tj("engine/static/engine/js/siem.js:169:50", { v1: etat.chaine_systeme.premier_bloc_fautif }), true);
    if (etat.derniere_verification && !etat.derniere_verification.ok) bandeau(tj("engine/static/engine/js/siem.js:170:83", { v1: etat.derniere_verification.anomalies.length }), true);
    var inc = etat.incident || { etat: "calme", incident: null, recents: [] };
    if (inc.etat === "attaque") bandeau(tj("engine/static/engine/js/siem.js:172:45", { v1: new Date(inc.incident.ouvert * 1000).toLocaleTimeString(), v2: inc.incident.couches.join(", ") }), true);
    else if (inc.etat === "suspect") bandeau(tj("engine/static/engine/js/siem.js:173:50", { v1: new Date(inc.incident.ouvert * 1000).toLocaleTimeString(), v2: inc.incident.couches.join(", ") }));
    var maintenant = etat.maintenant;
    Object.keys(etat.battements).forEach(function (s) {
      var t = etat.battements[s];
      if (t === null || maintenant - t > etat.silence_collecteur_s) bandeau(t ? tj("engine/static/engine/js/siem.js:177:85", { v1: s, v2: Math.round(maintenant - t) }) : tj("engine/static/engine/js/siem.js:177:147", { v1: s }), true);
    });

    if ((etat.lots_masques || []).length) bandeau(tj("engine/static/engine/js/siem.js:lots_bandeau", { v1: etat.lots_masques.length }));
    bandeauxSante(sante);
    racine.textContent = "";

    // ── Synthèse : la situation en un coup d'œil ──
    var mot = inc.etat === "attaque" ? tj("engine/static/engine/js/siem.js:195:99") : inc.etat === "suspect" ? tj("engine/static/engine/js/siem.js:195:146") : tj("engine/static/engine/js/siem.js:195:168");
    var niveauHero = inc.etat === "attaque" ? "faible" : inc.etat === "suspect" ? "moyen" : "ok";
    var protections = [
      etat.protections.double_facteur ? tj("engine/static/engine/js/siem.js:189:47") : tj("engine/static/engine/js/siem.js:189:65"), etat.protections.ip_restreintes ? tj("engine/static/engine/js/siem.js:189:120") : tj("engine/static/engine/js/siem.js:189:139"),
      etat.protections.ancrage_fichier ? tj("engine/static/engine/js/siem.js:190:48") : "", etat.protections.ancrage_distant ? tj("engine/static/engine/js/siem.js:190:107") : "", etat.protections.cle_environnement ? tj("engine/static/engine/js/siem.js:190:168") : tj("engine/static/engine/js/siem.js:190:190")
    ].filter(Boolean).join(" · ");
    var a24 = etat.alertes_24h;
    racine.appendChild(el("div", { classe: "dc-hero" }, [
      el("div", { classe: "hero-tete" }, [
        el("div", { classe: "hero-chiffre mot " + niveauHero }, [el("b", { texte: mot })]),
        el("div", { classe: "hero-texte" }, [
          el("span", { classe: "badge " + (etat.mode === "actif" ? "ok" : "warn"), texte: tj("engine/static/engine/js/siem.js:183:17") + " : " + (etat.mode === "actif" ? tj("engine/static/engine/js/siem.js:183:59") : tj("engine/static/engine/js/siem.js:183:78")) }),
          el("p", { texte: tj("engine/static/engine/js/siem.js:188:17") + " : " + protections })
        ])
      ]),
      el("div", { classe: "chiffres" }, [
        chiffre(tj("engine/static/engine/js/siem.js:184:17"), etat.chaine_systeme.valide ? tj("engine/static/engine/js/siem.js:184:66", { v1: etat.chaine_systeme.blocs }) : tj("engine/static/engine/js/siem.js:184:123"), etat.chaine_systeme.valide ? "sm-ok" : "sm-ko"),
        chiffre(tj("engine/static/engine/js/siem.js:187:17"), tj("engine/static/engine/js/siem.js:187:40", { v1: a24.critique, v2: a24.haute, v3: a24.moyenne }), a24.critique ? "sm-ko" : a24.haute ? "sm-av" : "sm-ok"),
        chiffre(tj("engine/static/engine/js/siem.js:186:17"), etat.derniere_verification ? (etat.derniere_verification.ok ? tj("engine/static/engine/js/siem.js:186:98", { v1: date(etat.derniere_verification.t) }) : tj("engine/static/engine/js/siem.js:186:163", { v1: date(etat.derniere_verification.t) })) : tj("engine/static/engine/js/siem.js:186:230"), etat.derniere_verification && etat.derniere_verification.ok ? "sm-ok" : "sm-av"),
        chiffre(tj("engine/static/engine/js/siem.js:185:17"), etat.dernier_ancrage ? tj("engine/static/engine/js/siem.js:185:61", { v1: date(etat.dernier_ancrage.t), v2: etat.dernier_ancrage.index }) : tj("engine/static/engine/js/siem.js:185:155"), etat.dernier_ancrage ? "" : "sm-av")
      ])
    ]));

    // ── Actions : l'essentiel visible, la maintenance rangée dans « Plus d'actions » ──
    var verrou = etat.lockdown;
    var rangees = el("div", { classe: "dc-plus-liste" }, [
      bouton(etat.mode === "actif" ? tj("engine/static/engine/js/siem.js:223:42") : tj("engine/static/engine/js/siem.js:223:66"), function () {
        agir("/api/siem/config", { config: { mode: etat.mode === "actif" ? "observation" : "actif" } }, tj("engine/static/engine/js/siem.js:224:109"));
      }),
      bouton(tj("engine/static/engine/js/siem.js:226:18"), function () { agir("/api/siem/rebaser", { cible: "code" }, tj("engine/static/engine/js/siem.js:226:96")); }),
      bouton(tj("engine/static/engine/js/siem.js:229:18"), function () { agir("/api/siem/rotation", {}, tj("engine/static/engine/js/siem.js:229:86")); })
    ]);
    racine.appendChild(el("div", { classe: "barre-actions" }, [
      bouton(verrou ? tj("engine/static/engine/js/siem.js:220:34") : tj("engine/static/engine/js/siem.js:220:55"), function () { agir("/api/siem/lockdown", { actif: !verrou }, verrou ? tj("engine/static/engine/js/siem.js:220:162") : tj("engine/static/engine/js/siem.js:220:185")); }, verrou ? "principal" : "danger"),
      bouton(tj("engine/static/engine/js/siem.js:221:18"), function () { agir("/api/siem/verifier"); }, verrou ? "" : "principal"),
      bouton(tj("engine/static/engine/js/siem.js:222:18"), function () { agir("/api/siem/cycle"); }),
      el("details", { classe: "dc-plus" }, [el("summary", { texte: racine.getAttribute("data-plus") || "…" }), rangees])
    ]));

    dessinerLots(etat.lots_masques || []);

    // ── Incident en cours (ouvert par défaut : c'est la question d'un décideur) ──
    var contenuInc = [];
    if (inc.incident) {
      var courant = inc.incident;
      contenuInc.push(el("div", { classe: "dc-cartes" }, [
        carte(tj("engine/static/engine/js/siem.js:199:19"), date(courant.ouvert), inc.etat === "attaque" ? "sm-ko" : "sm-av"),
        carte(tj("engine/static/engine/js/siem.js:200:19"), courant.couches.join(", ")),
        carte(tj("engine/static/engine/js/siem.js:201:19"), courant.ips.length ? courant.ips.join(", ") : tj("engine/static/engine/js/siem.js:201:70")),
        carte(tj("engine/static/engine/js/siem.js:202:19"), courant.reponses.length ? courant.reponses.join(" · ") : tj("engine/static/engine/js/siem.js:202:82")),
        carte(tj("engine/static/engine/js/siem.js:203:19"), tj("engine/static/engine/js/siem.js:203:40", { v1: Math.ceil(courant.calme_dans_s / 60) }))
      ]));
      contenuInc.push(el("div", { classe: "barre-actions" }, [bouton(tj("engine/static/engine/js/siem.js:205:72"), function () { agir("/api/siem/incident/clore", {}, tj("engine/static/engine/js/siem.js:205:143")); })]));
      contenuInc.push(tableau([tj("engine/static/engine/js/siem.js:206:39"), tj("engine/static/engine/js/siem.js:206:58"), tj("engine/static/engine/js/siem.js:206:79"), tj("engine/static/engine/js/siem.js:206:101"), tj("engine/static/engine/js/siem.js:206:121"), tj("engine/static/engine/js/siem.js:206:143")], courant.signaux.map(function (x) {
        return [date(x.t), x.couche, el("span", { classe: "dc-g dc-g-" + x.gravite, texte: x.gravite }), x.regle, x.message, x.ip || ""];
      })));
    } else {
      contenuInc.push(el("p", { texte: tj("engine/static/engine/js/siem.js:210:47"), classe: "dc-ok" }));
    }
    racine.appendChild(section(tj("engine/static/engine/js/siem.js:195:46", { v1: mot }), contenuInc, true));
    if (inc.recents && inc.recents.length) {
      racine.appendChild(section(tj("engine/static/engine/js/siem.js:213:48"), [tableau([tj("engine/static/engine/js/siem.js:214:39"), tj("engine/static/engine/js/siem.js:214:60"), tj("engine/static/engine/js/siem.js:214:79"), tj("engine/static/engine/js/siem.js:214:100"), tj("engine/static/engine/js/siem.js:214:122"), tj("engine/static/engine/js/siem.js:214:144"), tj("engine/static/engine/js/siem.js:214:161")], inc.recents.map(function (r) {
        return [date(r.ouvert), date(r.ferme), r.niveau, (r.couches || []).join(", "), r.nb_signaux, (r.ips || []).join(", "), r.motif];
      }))], false, inc.recents.length));
    }

    // ── Santé du moteur ──
    dessinerSante(sante);

    // ── Alertes et réponses (ouvert s'il y a du critique) ──
    racine.appendChild(section(tj("engine/static/engine/js/siem.js:232:46"), [barreLots(etat.lots_masques || []), tableau([tj("engine/static/engine/js/siem.js:233:37"), tj("engine/static/engine/js/siem.js:233:56"), tj("engine/static/engine/js/siem.js:233:78"), tj("engine/static/engine/js/siem.js:233:105"), tj("engine/static/engine/js/siem.js:233:127"), tj("engine/static/engine/js/siem.js:233:144")], alertes.map(function (a) {
      return [date(a.ts) + (a.masque ? " · " + tj("engine/static/engine/js/siem.js:masque_etiquette") : ""), el("span", { classe: "dc-g dc-g-" + (a.gravite || "info"), texte: a.gravite || a.action.replace("siem_", "") }), a.regle || a.action,
        a.message || (a.actions ? ((a.simulee ? tj("engine/static/engine/js/siem.js:235:53") : "") + a.actions.join(", ")) : ""), a.ip || "", "#" + a.bloc];
    }))], !!a24.critique || rechActive, alertes.length));

    // ── Scores de risque (ouvert s'il y a quelque chose à déverrouiller) ──
    var lignesRisque = risques.comptes.map(function (c) {
      return [tj("engine/static/engine/js/siem.js:240:19"), c.nom, c.score, c.verrouille ? tj("engine/static/engine/js/siem.js:240:70") : "", c.verrouille ? bouton(tj("engine/static/engine/js/siem.js:240:119"), function () { agir("/api/siem/deverrouiller", { type: "compte", cle: c.cle }); }, "petit") : ""];
    }).concat(risques.ips.map(function (i) {
      return ["IP", i.cle, i.score, i.bloquee_s ? tj("engine/static/engine/js/siem.js:242:55", { v1: Math.ceil(i.bloquee_s / 60) }) : "", i.bloquee_s ? bouton(tj("engine/static/engine/js/siem.js:242:137"), function () { agir("/api/siem/deverrouiller", { type: "ip", cle: i.cle }); }, "petit") : ""];
    }));
    racine.appendChild(section(tj("engine/static/engine/js/siem.js:238:46"), [tableau([tj("engine/static/engine/js/siem.js:244:37"), tj("engine/static/engine/js/siem.js:244:56"), tj("engine/static/engine/js/siem.js:244:76"), tj("engine/static/engine/js/siem.js:244:96"), ""], lignesRisque)], lignesRisque.some(function (l) { return l[4] !== ""; }), lignesRisque.length));

    // ── Modèle fantôme : apprend des comportements, de la messagerie et de l'état du système (les 50 règles lui servent de professeur) ──
    if (fantome) dessinerFantome(fantome);

    // ── Événements collectés ──
    racine.appendChild(section(tj("engine/static/engine/js/siem.js:246:46"), [tableau([tj("engine/static/engine/js/siem.js:247:37"), tj("engine/static/engine/js/siem.js:247:56"), tj("engine/static/engine/js/siem.js:247:75"), tj("engine/static/engine/js/siem.js:247:96"), tj("engine/static/engine/js/siem.js:247:121"), tj("engine/static/engine/js/siem.js:247:138")], evenements.map(function (e) {
      var d = e.details || {};
      return [date(e.ts) + (e.masque ? " · " + tj("engine/static/engine/js/siem.js:masque_etiquette") : ""), e.type, e.utilisateur || "", e.entreprise, e.ip, (d.methode ? d.methode + " " : "") + (d.chemin || "") + (d.statut ? " → " + d.statut : "")];
    }))], rechActive, evenements.length));

    // ── Seuils de détection ──
    var grille = el("div", { classe: "dc-seuils" });
    var champs = {};
    Object.keys(etat.seuils).forEach(function (k) {
      var champ = el("input", { type: "number", step: "any", value: etat.seuils[k] });
      champs[k] = champ;
      grille.appendChild(el("div", {}, [el("label", { texte: k }), champ]));
    });
    racine.appendChild(section(tj("engine/static/engine/js/siem.js:252:46"), [grille, el("div", { classe: "barre-actions" }, [bouton(tj("engine/static/engine/js/siem.js:261:70"), function () {
      var s = {}; Object.keys(champs).forEach(function (k) { s[k] = champs[k].value; });
      agir("/api/siem/config", { config: { seuils: s } });
    })])], false));
  }

  function raisons(l) { return (l || []).map(function (r) { return r.mesure + " (" + (r.ecart > 0 ? "+" : "") + r.ecart + ")"; }).join(", "); }

  function dessinerFantome(f) {
    var noms = { systeme: tj("engine/static/engine/js/siem.js:450:28"), compte: tj("engine/static/engine/js/siem.js:lbl_comportements"), message: tj("engine/static/engine/js/siem.js:450:73") };
    var contenu = [el("p", { classe: "aide", texte: tj("engine/static/engine/js/siem.js:451:54") })];
    contenu.push(el("div", { classe: "dc-cartes" }, Object.keys(f.tetes).map(function (n) {
      var t = f.tetes[n];
      var cal = (f.calibrage || {})[n];
      var retenus = (f.retenus || {})[n];
      return carte(noms[n] || n, t.echantillons + tj("engine/static/engine/js/siem.js:456:52") + t.suspects + " suspects, " + t.calmes + " calmes) · " + t.avis_ou_retours + tj("engine/static/engine/js/siem.js:456:148") +
        (t.perte === null ? "" : " · perte " + t.perte) +
        (cal ? " · seuil " + cal.seuil + " (" + cal.source + ")" : "") + (retenus ? " · " + retenus + tj("engine/static/engine/js/siem.js:458:104") : ""), t.mature ? "sm-ok" : "sm-av");
    })));
    if (f.votants && f.votants.sans_credit) contenu.push(el("p", { texte: f.votants.sans_credit + tj("engine/static/engine/js/siem.js:460:100") + f.votants.suivis + tj("engine/static/engine/js/siem.js:460:139") }));
    if (f.systeme) contenu.push(el("p", { texte: tj("engine/static/engine/js/siem.js:461:51") + f.systeme.score + (f.systeme.raisons.length ? tj("engine/static/engine/js/siem.js:461:126") + raisons(f.systeme.raisons) : "") }));
    contenu.push(tableau([tj("engine/static/engine/js/siem.js:462:28"), "Score", tj("engine/static/engine/js/siem.js:462:47"), ""], f.comptes.map(function (c) {
      return [c.nom, c.score, raisons(c.raisons), bouton(tj("engine/static/engine/js/siem.js:463:59"), function () { agir("/api/siem/deverrouiller", { type: "compte", cle: c.cle }, tj("engine/static/engine/js/siem.js:463:153")); }, "petit")];
    })));
    contenu.push(tableau([tj("engine/static/engine/js/siem.js:465:28"), tj("engine/static/engine/js/siem.js:465:42"), "Score", tj("engine/static/engine/js/siem.js:465:65")], f.messages.map(function (m) { return [m.cle, m.expediteur, m.score + (m.retenu ? " (retenu)" : ""), raisons(m.raisons)]; })));
    racine.appendChild(section(tj("engine/static/engine/js/siem.js:466:33"), contenu, f.comptes.length + f.messages.length > 0, f.comptes.length + f.messages.length));
  }

  function charger() {
    var rq = chaineRecherche(), suite = rq ? "&" + rq : "";
    Promise.all([api("/api/siem/etat" + (avecMasques ? "?avec_masques=1" : "")), api("/api/siem/alertes?limite=100" + suite), api("/api/siem/risques"),
      api("/api/siem/evenements?limite=100" + suite), api("/api/siem/sante").catch(function () { return null; }), api("/api/siem/fantome").catch(function () { return null; })])
      .then(function (r) {
        dessiner(r[0], r[1].evenements, r[2], r[3].evenements, r[4], r[5]);
        remplirTypes((r[1].types || []).concat(r[3].types || []).filter(function (t, i, a) { return a.indexOf(t) === i; }));
        resumeRecherche((r[1].nb || 0) + (r[3].nb || 0), (r[1].nb_masques || 0) + (r[3].nb_masques || 0));
        appliquerRecherche();
      })
      .catch(function (e) { racine.textContent = e.message; });
  }

  brancherFormulaire();
  charger();
  minuterie = window.setInterval(function () { if (fenetreReauth.style.display !== "flex") charger(); }, 30000);
  window.addEventListener("beforeunload", function () { window.clearInterval(minuterie); });
})();
