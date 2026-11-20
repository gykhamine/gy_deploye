// Studio d'édition visuelle du formulaire de saisie d'une table, façon Google Forms : à gauche l'APERÇU FIDÈLE (même moteur de rendu que
// le formulaire client, static/engine/js/formulaire_theme.js), à droite trois onglets — Design (style, couleurs, image de garde, image
// de fond), Texte (police, gras / italique / souligné, taille, couleur, alignement de chaque élément cliqué dans l'aperçu) et Parcours
// (nature « Standard » ou « Conditionnel » : sections + renvois selon les réponses). La TABLE ne change jamais : mêmes colonnes ; les
// champs des sections qu'une réponse fait sauter restent simplement vides. Ouvert depuis la barre de la table, ou juste après la
// création d'une table de nature « Conditionnel ».
(function(){
  const K = "engine/static/engine/js/formulaire_conditionnel.js:";
  const T = (cle, vars) => tj(K + cle, vars);
  const E = (s) => escapeHtml(s);
  const SOUMETTRE = "soumettre", SUIVANTE = "suivante";

  let etat = null;   // {tableId, table, actif, sections:[{id,titre,description,champs:[slug],apres,branche}], champs:{slug:{...}}, theme, role, onglet, vue}
  let fond = null;

  const nouvelId = () => { let n = etat.sections.length + 1; const ids = new Set(etat.sections.map(s => s.id)); while(ids.has("s" + n)) n++; return "s" + n; };
  const rang = (id) => etat.sections.findIndex(s => s.id === id);
  const libelleSection = (s, i) => T("section_n", {v1: i + 1}) + (s.titre ? " — " + s.titre : "");

  // Un renvoi n'est valable que vers l'avant : après un déplacement / une suppression, on ramène les autres à « section suivante ».
  function reparer(){
    etat.sections.forEach((s, i) => {
      const ok = (c) => c === SOUMETTRE || c === SUIVANTE || (rang(c) > i);
      if(!ok(s.apres)) s.apres = SUIVANTE;
      if(s.branche){
        if(!s.champs.includes(s.branche.champ) || !etat.champs[s.branche.champ]) s.branche = null;
        else for(const v of Object.keys(s.branche.regles)) if(!ok(s.branche.regles[v])) s.branche.regles[v] = SUIVANTE;
      }
    });
  }

  function optionsCible(i, valeur, avecDefaut){
    const o = [];
    if(avecDefaut) o.push(["", T("cible_defaut")]);
    o.push([SUIVANTE, T("cible_suivante")]);
    for(let j = i + 2; j < etat.sections.length; j++) o.push([etat.sections[j].id, T("cible_aller", {v1: libelleSection(etat.sections[j], j)})]);
    o.push([SOUMETTRE, T("cible_envoyer")]);
    return o.map(([v, l]) => `<option value="${E(v)}"${v === (valeur || "") ? " selected" : ""}>${E(l)}</option>`).join("");
  }

  function rendreSection(s, i){
    const n = etat.sections.length;
    const branchables = s.champs.map(sl => etat.champs[sl]).filter(c => c && c.valeurs && c.valeurs.length);
    const b = s.branche;
    const champ = b ? etat.champs[b.champ] : null;
    const lignesChamps = s.champs.map((sl, k) => {
      const c = etat.champs[sl]; if(!c) return "";
      const versSection = etat.sections.map((x, j) => `<option value="${j}"${j === i ? " selected" : ""}>${E(T("section_n", {v1: j + 1}))}</option>`).join("");
      return `<div class="fc-champ" data-slug="${E(sl)}">
        <span class="fc-champ-nom">${E(c.nom)} <em>${E(c.type)}${c.obligatoire ? " *" : ""}</em></span>
        <span class="fc-champ-actions">
          <button type="button" data-act="champ-haut" ${k === 0 ? "disabled" : ""} title="${E(T("monter"))}">▲</button>
          <button type="button" data-act="champ-bas" ${k === s.champs.length - 1 ? "disabled" : ""} title="${E(T("descendre"))}">▼</button>
          <select data-act="champ-vers" title="${E(T("deplacer_vers"))}">${versSection}</select>
        </span></div>`;
    }).join("") || `<div class="fc-vide">${E(T("section_vide"))}</div>`;
    const regles = champ ? champ.valeurs.map(v => `<div class="fc-regle"><span>${E(T("si_reponse", {v1: v.libelle}))}</span>
        <select data-act="regle" data-valeur="${E(v.valeur)}">${optionsCible(i, b.regles[v.valeur] || "", true)}</select></div>`).join("") : "";
    return `<div class="fc-section" data-i="${i}">
      <div class="fc-section-tete">
        <strong>${E(T("section_sur", {v1: i + 1, v2: n}))}</strong>
        <span class="fc-section-actions">
          <button type="button" data-act="sec-haut" ${i === 0 ? "disabled" : ""} title="${E(T("monter_section"))}">▲</button>
          <button type="button" data-act="sec-bas" ${i === n - 1 ? "disabled" : ""} title="${E(T("descendre_section"))}">▼</button>
          <button type="button" class="danger" data-act="sec-suppr" ${n === 1 ? "disabled" : ""} title="${E(T("supprimer_section_titre"))}">✕</button>
        </span>
      </div>
      <input type="text" class="fc-titre" data-act="titre" maxlength="200" placeholder="${E(T("titre_ph"))}" value="${E(s.titre)}">
      <textarea class="fc-desc" data-act="desc" maxlength="1000" rows="2" placeholder="${E(T("desc_ph"))}">${E(s.description)}</textarea>
      <div class="fc-champs">${lignesChamps}</div>
      <div class="fc-flux">
        <label>${E(T("apres_section"))}
          <select data-act="apres">${i === n - 1 ? `<option value="${SOUMETTRE}" selected>${E(T("cible_envoyer"))}</option>` : optionsCible(i, s.apres, false)}</select></label>
        ${i < n - 1 && branchables.length ? `<label>${E(T("selon_reponse"))}
          <select data-act="branche-champ"><option value="">${E(T("aucune_branche"))}</option>
            ${branchables.map(c => `<option value="${E(c.slug)}"${b && b.champ === c.slug ? " selected" : ""}>${E(c.nom)}</option>`).join("")}</select></label>` : ""}
        ${i < n - 1 ? regles : ""}
      </div>
    </div>`;
  }

  const ROLES = ["titre", "description", "section", "question", "saisie"];
  const GT = () => window.gyFormTheme;
  const erreur = (m) => { if(window.notifierErreur) window.notifierErreur(m); };
  const stRole = (role) => GT().resoudre(etat.theme).styles[role];
  function reglerStyle(role, cle, val){
    etat.theme.styles = etat.theme.styles || {};
    etat.theme.styles[role] = etat.theme.styles[role] || {};
    if(val === null) delete etat.theme.styles[role][cle]; else etat.theme.styles[role][cle] = val;
  }

  // Apparence propre à une section (theme.sections[id]) et taille des polices propre à un champ (theme.champs[slug]).
  function reglerSection(id, cle, val){
    etat.theme.sections = etat.theme.sections || {};
    const st = etat.theme.sections[id] = etat.theme.sections[id] || {};
    if(val === null) delete st[cle]; else st[cle] = val;
    if(!st.image) delete st.voile;
    if(!Object.keys(st).length) delete etat.theme.sections[id];
  }
  function reglerChamp(slug, cle, val){
    etat.theme.champs = etat.theme.champs || {};
    const st = etat.theme.champs[slug] = etat.theme.champs[slug] || {};
    if(val === null) delete st[cle]; else st[cle] = val;
    if(!Object.keys(st).length) delete etat.theme.champs[slug];
  }

  // ── Aperçu : même rendu que sur la page client ────────────────────────────────────────────────────────────────
  function champApercu(c){
    const t = c.type || "", nom = E(c.nom);
    let w;
    if(/booleen|switch/.test(t)) w = `<label class="choix-item"><input type="checkbox" disabled tabindex="-1"> ${nom}</label>`;
    else if(/choix|liste/.test(t)) w = `<select class="champ" disabled tabindex="-1"><option>${E((c.valeurs && c.valeurs[0] && c.valeurs[0].libelle) || "")}</option></select>`;
    else if(/long|zone|multiligne/.test(t)) w = `<textarea class="champ" rows="2" readonly tabindex="-1"></textarea>`;
    else if(/image|son|video|fichier|pdf|document/.test(t)) w = `<div class="upload-zone" style="padding:14px;text-align:center">…</div>`;
    else w = `<input class="champ" type="${/date/.test(t) ? "date" : /nombre|entier|decimal|montant/.test(t) ? "number" : "text"}" readonly tabindex="-1">`;
    return `<div class="groupe" data-slug="${E(c.slug)}"><label>${nom}${c.obligatoire ? ' <span class="req">*</span>' : ""}</label>${w}</div>`;
  }

  function rendreApercu(){
    const scene = fond.querySelector(".fc-scene");
    scene.querySelector(".form-titre").textContent = etat.table.nom || "";
    scene.querySelector(".form-desc").textContent = etat.table.description || "";
    const n = etat.sections.length;
    if(etat.vue >= n) etat.vue = n - 1;
    if(etat.vue < 0) etat.vue = 0;
    const s = etat.sections[etat.vue];
    const slugs = etat.actif ? s.champs : etat.sections.flatMap(x => x.champs);
    const tete = etat.actif
      ? `<div class="form-section-tete"><h3 contenteditable="true" data-ph="${E(T("apercu_titre_section_ph"))}" data-edit="titre"></h3><p contenteditable="true" data-ph="${E(T("apercu_desc_section_ph"))}" data-edit="desc"></p></div>` : "";
    const zone = scene.querySelector(".gyf-apercu-champs");
    const corpsApercu = tete + (slugs.map(sl => etat.champs[sl]).filter(Boolean).map(champApercu).join("") || `<div class="fc-vide">${E(T("apercu_vide"))}</div>`);
    zone.innerHTML = etat.actif ? `<div class="form-section" data-section-id="${E(s.id)}">${corpsApercu}</div>` : corpsApercu;   // enveloppe = ce qui reçoit l'apparence de la section
    if(etat.actif){ zone.querySelector("[data-edit=titre]").textContent = s.titre; zone.querySelector("[data-edit=desc]").textContent = s.description; }
    const nav = fond.querySelector(".fc-apercu-nav");
    nav.style.display = etat.actif && n > 1 ? "" : "none";
    nav.innerHTML = `<button type="button" data-vue="-1" ${etat.vue === 0 ? "disabled" : ""} title="${E(T("apercu_precedente"))}">◀</button>
      <span>${E(T("apercu_section", {v1: etat.vue + 1, v2: n}))}</span>
      <button type="button" data-vue="1" ${etat.vue === n - 1 ? "disabled" : ""} title="${E(T("apercu_suivante"))}">▶</button>`;
    fond.querySelector(".fc-apercu").dataset.sel = etat.role;
    GT().appliquer(scene, etat.theme);
    zone.querySelectorAll(".groupe").forEach(g => { if(g.dataset.slug === etat.champSel) g.dataset.champSel = "1"; });
  }

  // ── Panneau de droite ──────────────────────────────────────────────────────────────────────────────────────────
  function panneauDesign(){
    const th = GT().resoudre(etat.theme);
    const presets = ["flat", "verre", "papier", "nuit", "minimal"].map(k => { const p = GT().PRESETS[k];
      return `<button type="button" class="fc-preset${th.preset === k ? " actif" : ""}" data-preset="${k}" style="--pfond:${p.fond};--pcarte:${p.carte}" title="${E(T("preset_" + k))}"><i></i></button>`; }).join("");
    const couleurs = [["fond", "col_fond"], ["carte", "col_carte"], ["texte", "col_texte"], ["accent", "col_accent"]]
      .map(([k, l]) => `<div class="fc-ligne"><span>${E(T(l))}</span><input type="color" data-couleur="${k}" value="${E(th[k])}"></div>`).join("");
    const rg = (k, l, mn, mx) => `<div class="fc-ligne"><span>${E(T(l))}</span><input type="range" min="${mn}" max="${mx}" data-reglage="${k}" value="${th[k]}"></div>`;
    return `<div class="fc-bloc"><h4>${E(T("preset_label"))}</h4><div class="fc-presets">${presets}</div></div>
      <div class="fc-bloc"><h4>${E(T("couleurs_label"))}</h4>${couleurs}</div>
      <div class="fc-bloc"><h4>${E(T("images_label"))}</h4>
        <div class="fc-ligne"><span>${E(T("image_garde"))}</span><span class="fc-boutons"><button type="button" data-image="garde">${E(T("choisir"))}</button>${th.image_garde ? `<button type="button" data-retirer="garde">${E(T("retirer"))}</button>` : ""}</span></div>
        ${th.image_garde ? rg("garde_hauteur", "garde_hauteur", 120, 520) : `<label class="fc-ligne"><span>${E(T("illustration"))}</span><input type="checkbox" data-chk="illustration" ${th.illustration ? "checked" : ""}></label>`}
        <div class="fc-ligne"><span>${E(T("image_fond"))}</span><span class="fc-boutons"><button type="button" data-image="fond">${E(T("choisir"))}</button>${th.image_fond ? `<button type="button" data-retirer="fond">${E(T("retirer"))}</button>` : ""}</span></div>
        ${th.image_fond ? rg("voile", "voile", 0, 90) + rg("flou", "flou", 0, 24) : ""}</div>
      <div class="fc-bloc"><h4>${E(T("forme_label"))}</h4>${rg("rayon", "rayon", 0, 40)}
        <div class="fc-ligne"><span>${E(T("colonnes_label"))}</span><select data-selcol><option value="1"${th.colonnes === 1 ? " selected" : ""}>${E(T("colonnes_1"))}</option><option value="2"${th.colonnes === 2 ? " selected" : ""}>${E(T("colonnes_2"))}</option></select></div></div>
      <div class="fc-boutons"><button type="button" data-reinit>${E(T("reinitialiser_design"))}</button></div>`;
  }

  // Taille des polices propre au champ cliqué dans l'aperçu (libellé et saisie), indépendante de celle du rôle.
  function blocChamp(){
    const c = etat.champSel && etat.champs[etat.champSel];
    if(!c) return `<p class="fc-aide">${E(T("champ_taille_aide"))}</p>`;
    const th = GT().resoudre(etat.theme), cs = (etat.theme.champs || {})[c.slug] || {};
    const rg = (k, l, v) => `<div class="fc-ligne"><span>${E(T(l))}</span><input type="range" min="10" max="48" data-ctaille="${k}" value="${v}"></div>`;
    return `<div class="fc-bloc"><h4>${E(T("champ_taille_titre", {v1: c.nom}))}</h4>
      ${rg("taille_question", "champ_taille_question", cs.taille_question || th.styles.question.taille)}
      ${rg("taille_saisie", "champ_taille_saisie", cs.taille_saisie || th.styles.saisie.taille)}
      <div class="fc-boutons"><button type="button" data-cauto>${E(T("couleur_auto"))}</button></div></div>`;
  }

  function panneauTexte(){
    const st = stRole(etat.role);
    const roles = ROLES.map(r => `<button type="button" data-role="${r}" class="${r === etat.role ? "actif" : ""}">${E(T("role_" + r))}</button>`).join("");
    const polices = Object.keys(GT().POLICES).map(k => `<option value="${k}" style="font-family:${E(GT().POLICES[k])}"${st.police === k ? " selected" : ""}>${E(T("pol_" + k))}</option>`).join("");
    const al = (v, sym, cle) => `<button type="button" data-align="${v}" class="${st.align === v ? "actif" : ""}" title="${E(T(cle))}">${sym}</button>`;
    return `<p class="fc-aide-role">${E(T("role_aide"))}</p><div class="fc-roles">${roles}</div>
      <div class="fc-fmt">
        <button type="button" data-fmt="gras" class="${st.gras ? "actif" : ""}" title="${E(T("gras"))}"><b>B</b></button>
        <button type="button" data-fmt="italique" class="${st.italique ? "actif" : ""}" title="${E(T("italique"))}"><i>I</i></button>
        <button type="button" data-fmt="souligne" class="${st.souligne ? "actif" : ""}" title="${E(T("souligne"))}"><u>U</u></button>
        ${al("left", "⇤", "aligner_g")}${al("center", "⇔", "aligner_c")}${al("right", "⇥", "aligner_d")}
      </div>
      <div class="fc-ligne"><span>${E(T("police"))}</span><select data-police>${polices}</select></div>
      <div class="fc-ligne"><span>${E(T("taille"))}</span><input type="range" min="10" max="72" data-taille value="${st.taille}"></div>
      <div class="fc-ligne"><span>${E(T("couleur_texte"))}</span><input type="color" data-rcouleur value="${E(st.couleur || GT().resoudre(etat.theme).texte)}">
        <button type="button" data-rauto>${E(T("couleur_auto"))}</button></div>
      ${blocChamp()}`;
  }

  function panneauSections(){
    if(!etat.actif) return `<p class="fc-aide">${E(T("sections_style_inactif"))}</p>`;
    const th = GT().resoudre(etat.theme);
    const s = etat.sections[etat.vue], st = (etat.theme.sections || {})[s.id] || {};
    const choix = etat.sections.map((x, j) => `<option value="${j}"${j === etat.vue ? " selected" : ""}>${E(libelleSection(x, j))}</option>`).join("");
    const col = (k, l, defaut) => `<div class="fc-ligne"><span>${E(T(l))}</span><input type="color" data-scouleur="${k}" value="${E(st[k] || defaut)}"><button type="button" data-sauto="${k}" title="${E(T("couleur_auto"))}">${E(T("couleur_auto"))}</button></div>`;
    const rg = (k, l, mn, mx, v) => `<div class="fc-ligne"><span>${E(T(l))}</span><input type="range" min="${mn}" max="${mx}" data-sreglage="${k}" value="${v}"></div>`;
    return `<div class="fc-bloc"><h4>${E(T("section_choisie"))}</h4>
        <div class="fc-ligne"><select data-secvue>${choix}</select></div></div>
      <div class="fc-bloc"><h4>${E(T("sec_couleurs_label"))}</h4>
        ${col("fond", "sec_fond", th.carte)}${col("bordure", "sec_bordure", th.accent)}</div>
      <div class="fc-bloc"><h4>${E(T("sec_textes_label"))}</h4>
        ${col("titre", "sec_titre", th.styles.section.couleur || th.texte)}${col("description", "sec_description", th.styles.description.couleur || th.texte)}
        ${col("question", "sec_question", th.styles.question.couleur || th.texte)}${col("saisie", "sec_saisie", th.styles.saisie.couleur || th.texte)}</div>
      <div class="fc-bloc"><h4>${E(T("sec_forme_label"))}</h4>${rg("rayon", "sec_rayon", 0, 40, st.rayon === undefined ? th.rayon : st.rayon)}</div>
      <div class="fc-bloc"><h4>${E(T("sec_image_label"))}</h4>
        <div class="fc-ligne"><span>${E(T("sec_image"))}</span><span class="fc-boutons"><button type="button" data-simage>${E(T("choisir"))}</button>${st.image ? `<button type="button" data-sretirer>${E(T("retirer"))}</button>` : ""}</span></div>
        ${st.image ? rg("voile", "sec_voile", 0, 90, st.voile === undefined ? 35 : st.voile) : ""}</div>
      <div class="fc-boutons"><button type="button" data-sreinit>${E(T("sec_reinit"))}</button></div>`;
  }

  function panneauParcours(){
    return `<label class="fc-nature">${E(T("nature_label"))}
        <select data-act="nature">
          <option value="standard"${etat.actif ? "" : " selected"}>${E(T("nature_standard"))}</option>
          <option value="conditionnel"${etat.actif ? " selected" : ""}>${E(T("nature_conditionnel"))}</option>
        </select></label>
      <p class="fc-aide">${E(T("aide_table_inchangee"))}</p>
      <p class="fc-note-standard" style="display:${etat.actif ? "none" : ""}">${E(T("note_standard"))}</p>
      <div class="fc-editeur" style="display:${etat.actif ? "" : "none"}">
        <div class="fc-liste">${etat.sections.map(rendreSection).join("")}</div>
        <button type="button" data-act="ajouter-section" class="fc-ajouter">${E(T("ajouter_section"))}</button>
      </div>`;
  }

  function rendrePanneau(){
    const corps = fond.querySelector(".fc-panneau-corps"), haut = corps.scrollTop;
    corps.innerHTML = etat.onglet === "design" ? panneauDesign() : etat.onglet === "texte" ? panneauTexte() : etat.onglet === "sections" ? panneauSections() : panneauParcours();
    fond.querySelectorAll(".fc-onglets button").forEach(b => b.classList.toggle("actif", b.dataset.onglet === etat.onglet));
    corps.scrollTop = haut;
  }

  function rendre(){ rendreApercu(); rendrePanneau(); }

  // Réglages visuels (data-* du panneau) : mettent à jour le thème puis l'aperçu, sans reconstruire le panneau quand c'est inutile.
  function surReglage(ev){
    const el = ev.target, d = el.dataset, estClic = ev.type === "click", estSaisie = ev.type === "input", estChange = ev.type === "change";
    const maj = () => GT().appliquer(fond.querySelector(".fc-scene"), etat.theme);
    const btn = el.closest("button");
    if(estClic && btn){
      const b = btn.dataset;
      if(b.onglet){ etat.onglet = b.onglet; rendrePanneau(); return true; }
      if(b.preset){ etat.theme.preset = b.preset; for(const k of ["fond", "carte", "texte", "accent"]) delete etat.theme[k]; rendrePanneau(); maj(); return true; }
      if(b.image){ etat.cibleImage = b.image; fond.querySelector("[data-fichier]").click(); return true; }
      if(b.retirer){ delete etat.theme[b.retirer === "garde" ? "image_garde" : "image_fond"]; rendrePanneau(); maj(); return true; }
      if(b.reinit !== undefined){ etat.theme = {sections: etat.theme.sections, champs: etat.theme.champs}; if(!etat.theme.sections) delete etat.theme.sections; if(!etat.theme.champs) delete etat.theme.champs; rendrePanneau(); maj(); return true; }   // le design général se réinitialise ; l'apparence des sections et des champs reste
      if(b.simage !== undefined){ etat.cibleImage = "section"; fond.querySelector("[data-fichier]").click(); return true; }
      if(b.sretirer !== undefined){ reglerSection(etat.sections[etat.vue].id, "image", null); rendrePanneau(); maj(); return true; }
      if(b.sauto){ reglerSection(etat.sections[etat.vue].id, b.sauto, null); rendrePanneau(); maj(); return true; }
      if(b.sreinit !== undefined){ if(etat.theme.sections) delete etat.theme.sections[etat.sections[etat.vue].id]; rendrePanneau(); maj(); return true; }
      if(b.cauto !== undefined){ if(etat.champSel && etat.theme.champs) delete etat.theme.champs[etat.champSel]; rendrePanneau(); maj(); return true; }
      if(b.role){ etat.role = b.role; fond.querySelector(".fc-apercu").dataset.sel = b.role; rendrePanneau(); return true; }
      if(b.fmt){ reglerStyle(etat.role, b.fmt, !stRole(etat.role)[b.fmt]); rendrePanneau(); maj(); return true; }
      if(b.align){ reglerStyle(etat.role, "align", b.align); rendrePanneau(); maj(); return true; }
      if(b.rauto !== undefined){ reglerStyle(etat.role, "couleur", null); rendrePanneau(); maj(); return true; }
      if(b.vue){ etat.vue += parseInt(b.vue, 10); rendreApercu(); if(etat.onglet === "sections") rendrePanneau(); return true; }
    }
    if((estSaisie || estChange) && d.couleur){ etat.theme[d.couleur] = el.value; maj(); return true; }
    if((estSaisie || estChange) && d.scouleur){ reglerSection(etat.sections[etat.vue].id, d.scouleur, el.value); maj(); return true; }
    if(estSaisie && d.sreglage){ reglerSection(etat.sections[etat.vue].id, d.sreglage, parseInt(el.value, 10)); maj(); return true; }
    if(estSaisie && d.ctaille && etat.champSel){ reglerChamp(etat.champSel, d.ctaille, parseInt(el.value, 10)); maj(); return true; }
    if(estChange && d.secvue !== undefined){ etat.vue = parseInt(el.value, 10); rendre(); return true; }
    if(estSaisie && d.reglage){ etat.theme[d.reglage] = parseFloat(el.value); maj(); return true; }
    if(estSaisie && d.taille !== undefined){ reglerStyle(etat.role, "taille", parseInt(el.value, 10)); maj(); return true; }
    if((estSaisie || estChange) && d.rcouleur !== undefined){ reglerStyle(etat.role, "couleur", el.value); maj(); return true; }
    if(estChange && d.police !== undefined){ reglerStyle(etat.role, "police", el.value); maj(); return true; }
    if(estChange && d.chk === "illustration"){ etat.theme.illustration = el.checked; maj(); return true; }
    if(estChange && d.selcol !== undefined){ etat.theme.colonnes = parseInt(el.value, 10); maj(); return true; }
    if(estChange && d.fichier !== undefined){
      const f = el.files && el.files[0]; el.value = ""; if(!f) return true;
      if(etat.cibleImage === "section"){
        const sid = etat.sections[etat.vue].id;
        GT().imageVersDataUri(f, 1400, 330000)
          .then(uri => { reglerSection(sid, "image", uri); if(((etat.theme.sections || {})[sid] || {}).voile === undefined) reglerSection(sid, "voile", 35); rendrePanneau(); GT().appliquer(fond.querySelector(".fc-scene"), etat.theme); })
          .catch(e => erreur(T(e && e.message === "taille" ? "image_trop_lourde" : "image_invalide")));
        return true;
      }
      const cle = etat.cibleImage === "garde" ? "image_garde" : "image_fond";
      GT().imageVersDataUri(f, etat.cibleImage === "garde" ? 1400 : 1800, 850000)
        .then(uri => { etat.theme[cle] = uri; rendrePanneau(); GT().appliquer(fond.querySelector(".fc-scene"), etat.theme); })
        .catch(e => erreur(T(e && e.message === "taille" ? "image_trop_lourde" : "image_invalide")));
      return true;
    }
    return false;
  }

  // Clic dans l'aperçu : sélectionne l'élément (titre, question…) et ouvre l'onglet Texte ; saisie directe des titres de section.
  function roleDe(el){
    if(el.closest(".form-titre")) return "titre";
    if(el.closest(".form-desc") || el.closest(".form-section-tete p")) return "description";
    if(el.closest(".form-section-tete h3")) return "section";
    if(el.closest(".groupe label")) return "question";
    if(el.closest(".groupe")) return "saisie";
    return null;
  }
  function surApercu(ev){
    if(ev.type === "input"){
      const e = ev.target.closest("[data-edit]"); if(!e) return;
      const s = etat.sections[etat.vue];
      if(e.dataset.edit === "titre") s.titre = e.textContent.slice(0, 200); else s.description = e.textContent.slice(0, 1000);
      return;
    }
    if(ev.type === "focusout"){ if(ev.target.closest("[data-edit]") && etat.onglet === "parcours") rendrePanneau(); return; }
    if(ev.type === "keydown"){ if(ev.key === "Enter" && ev.target.dataset && ev.target.dataset.edit === "titre"){ ev.preventDefault(); ev.target.blur(); } return; }
    if(ev.type === "paste"){ if(ev.target.closest("[data-edit]")){ ev.preventDefault(); document.execCommand("insertText", false, (ev.clipboardData || window.clipboardData).getData("text")); } return; }
    if(ev.type === "click"){
      const g = ev.target.closest(".groupe[data-slug]");
      if(g){ etat.champSel = g.dataset.slug; fond.querySelectorAll(".fc-scene .groupe").forEach(x => { if(x === g) x.dataset.champSel = "1"; else delete x.dataset.champSel; }); }
      const r = roleDe(ev.target);
      if(!r){   // clic sur le fond d'une section : on ouvre l'onglet Sections
        if(etat.actif && ev.target.closest(".form-section")){ etat.onglet = "sections"; rendrePanneau(); }
        return;
      }
      etat.role = r; etat.onglet = "texte";
      fond.querySelector(".fc-apercu").dataset.sel = r; rendrePanneau();
    }
  }

  function deplacerChamp(slug, depuis, vers, position){
    const src = etat.sections[depuis].champs, k = src.indexOf(slug);
    if(k < 0) return;
    src.splice(k, 1);
    const dst = etat.sections[vers].champs;
    dst.splice(position === undefined ? dst.length : position, 0, slug);
  }

  function surAction(ev){
    const el = ev.target.closest("[data-act]"); if(!el) return;
    const secEl = el.closest(".fc-section"); const i = secEl ? parseInt(secEl.dataset.i, 10) : -1;
    const s = i >= 0 ? etat.sections[i] : null;
    const act = el.dataset.act;
    if(i >= 0 && etat.actif && i !== etat.vue && act !== "sec-haut" && act !== "sec-bas" && act !== "sec-suppr") etat.vue = i;
    const slug = el.closest(".fc-champ")?.dataset.slug;
    const estClic = ev.type === "click", estChange = ev.type === "change", estSaisie = ev.type === "input";

    if(estSaisie){ if(act === "titre"){ s.titre = el.value; } else if(act === "desc"){ s.description = el.value; } return; }
    if(estChange && act === "titre"){ rendre(); return; }   // le titre figure dans les listes de renvois : on les rafraîchit à la fin de la saisie
    if(estClic){
      if(act === "champ-haut" || act === "champ-bas"){
        const k = s.champs.indexOf(slug), d = act === "champ-haut" ? -1 : 1;
        if(k + d >= 0 && k + d < s.champs.length){ [s.champs[k], s.champs[k + d]] = [s.champs[k + d], s.champs[k]]; }
      } else if(act === "sec-haut" || act === "sec-bas"){
        const j = i + (act === "sec-haut" ? -1 : 1);
        if(j >= 0 && j < etat.sections.length){ [etat.sections[i], etat.sections[j]] = [etat.sections[j], etat.sections[i]]; reparer(); }
      } else if(act === "sec-suppr"){
        if(etat.sections.length > 1){
          const cible = i > 0 ? i - 1 : 1;   // ses champs passent dans la section précédente (ou la suivante si c'est la première)
          s.champs.slice().forEach(sl => deplacerChamp(sl, i, cible, i > 0 ? undefined : 0));
          etat.sections.splice(i, 1); reparer();
        }
      } else if(act === "ajouter-section"){
        etat.sections.push({id: nouvelId(), titre: "", description: "", champs: [], apres: SUIVANTE, branche: null});
      } else return;
      rendre(); return;
    }
    if(estChange){
      if(act === "champ-vers"){ deplacerChamp(slug, i, parseInt(el.value, 10)); reparer(); }
      else if(act === "apres"){ s.apres = el.value || SUIVANTE; }
      else if(act === "branche-champ"){ s.branche = el.value ? {champ: el.value, regles: {}} : null; }
      else if(act === "regle"){
        if(el.value) s.branche.regles[el.dataset.valeur] = el.value; else delete s.branche.regles[el.dataset.valeur];
      } else if(act === "nature"){ etat.actif = el.value === "conditionnel"; }
      else return;
      rendre();
    }
  }

  async function enregistrer(btn){
    reparer();
    const corps = {actif: etat.actif, sections: etat.sections.map(s => ({
      id: s.id, titre: s.titre.trim(), description: s.description.trim(), champs: s.champs, apres: s.apres,
      branche: s.branche && Object.keys(s.branche.regles).length ? s.branche : null,
    })), theme: etat.theme};
    btn.disabled = true;
    try{
      await api(`/api/tables/${etat.tableId}/structure-formulaire/`, {method: "POST", body: JSON.stringify(corps)});
      notifierSucces(T("enregistre"));
      fond.remove(); fond = null;
      if(window.rafraichirTables) rafraichirTables();
    } catch(e){ /* api() a déjà signalé l'erreur */ }
    finally{ btn.disabled = false; }
  }

  async function ouvrir(table, natureInitiale){
    if(fond){ fond.remove(); fond = null; }
    let data;
    try{ data = await api(`/api/tables/${table.id}/structure-formulaire/`); } catch(e){ return; }
    etat = {
      tableId: table.id,
      table: {nom: table.nom || "", description: table.description || ""},
      actif: natureInitiale ? natureInitiale === "conditionnel" : !!data.structure.actif,
      sections: data.structure.sections.map(s => ({id: s.id, titre: s.titre || "", description: s.description || "", champs: s.champs.slice(), apres: s.apres || SUIVANTE,
        branche: s.branche ? {champ: s.branche.champ, regles: Object.assign({}, s.branche.regles)} : null})),
      champs: Object.fromEntries(data.champs.map(c => [c.slug, c])),
      theme: JSON.parse(JSON.stringify(data.structure.theme || {})),
      role: "titre", onglet: natureInitiale === "conditionnel" ? "parcours" : "design", vue: 0, cibleImage: "garde", champSel: null,
    };
    fond = document.createElement("div");
    fond.className = "fc-fond fc-studio-fond";
    fond.innerHTML = `<div class="fc-studio" role="dialog" aria-modal="true">
      <div class="fc-studio-tete"><h2>${E(T("titre_modal", {v1: table.nom}))}</h2></div>
      <div class="fc-apercu" data-sel="titre">
        <p class="fc-apercu-aide">${E(T("apercu_aide"))}</p>
        <div class="fc-apercu-nav"></div>
        <div class="fc-scene"><div class="form-titre"></div><div class="form-desc"></div><div class="gyf-apercu-champs"></div>
          <div class="gyf-actions"><button type="button" class="gyf-btn" tabindex="-1">${E(T("apercu_envoyer"))}</button></div></div>
      </div>
      <div class="fc-panneau">
        <div class="fc-onglets">
          <button type="button" data-onglet="design">${E(T("onglet_design"))}</button>
          <button type="button" data-onglet="texte">${E(T("onglet_texte"))}</button>
          <button type="button" data-onglet="sections">${E(T("onglet_sections"))}</button>
          <button type="button" data-onglet="parcours">${E(T("onglet_parcours"))}</button>
        </div>
        <div class="fc-panneau-corps"></div>
      </div>
      <div class="fc-studio-pied">
        <button type="button" data-fermer>${E(T("annuler"))}</button>
        <button type="button" class="principal" data-enregistrer>${E(T("enregistrer_btn"))}</button>
      </div>
      <input type="file" accept="image/*" data-fichier hidden></div>`;
    fond.addEventListener("click", (ev) => {
      if(ev.target.hasAttribute("data-fermer")){ fond.remove(); fond = null; return; }
      if(ev.target.hasAttribute("data-enregistrer")){ enregistrer(ev.target); return; }
      if(ev.target.closest(".fc-scene")){ surApercu(ev); return; }
      if(surReglage(ev)) return;
      surAction(ev);
    });
    const relais = (ev) => { if(ev.target.closest(".fc-scene")){ surApercu(ev); return; } if(!surReglage(ev)) surAction(ev); };
    fond.addEventListener("change", relais);
    fond.addEventListener("input", relais);
    for(const type of ["focusout", "keydown", "paste"]) fond.addEventListener(type, (ev) => { if(ev.target.closest && ev.target.closest(".fc-scene")) surApercu(ev); });
    document.body.appendChild(fond);
    rendre();
  }

  window.gyFormulaireConditionnel = {ouvrir};
})();
