// Formulaire conditionnel côté agent / administrateur (page index) : mêmes sections et mêmes renvois que sur le portail client
// (formulaire_conditionnel_client.js), appliqués au formulaire d'ajout de ligne et à la vue « client » de l'index.
// La table ne change pas : les champs des sections non traversées sont ignorés (ni exigés, ni envoyés) ; le serveur recalcule le
// parcours à l'enregistrement (engine/formulaire_conditionnel.py : champs_hors_parcours) et fait foi.
// Les structures sont posées à la création / à la restructuration (éditeur gyFormulaireConditionnel) ; ici, lecture seule.
(function(){
  const K = "engine/static/engine/js/formulaire_conditionnel_client.js:";   // mêmes libellés (Retour / Suivant / Section n) que le portail client
  const T = (cle, vars) => window.tj ? window.tj(K + cle, vars) : cle;
  const SOUMETTRE = "soumettre", SUIVANTE = "suivante";

  const actif = (table) => !!(table && table.structure_formulaire && table.structure_formulaire.sections && table.structure_formulaire.sections.length);

  const canon = (v) => { if(Array.isArray(v)) v = v.length ? v[0] : ""; if(v === true) return "true"; if(v === false) return "false"; return v == null ? "" : String(v).trim(); };
  function cleRegle(regles, v){
    v = canon(v);
    if(v in regles) return v;
    if(v === "1" && "true" in regles) return "true";
    if(v === "0" && "false" in regles) return "false";
    return null;
  }

  function valeurDe(racine, slug){
    const el = racine.querySelector("#champ_" + slug);
    if(!el) return "";
    if(el.type === "checkbox") return el.checked;
    if(el.type === "radio"){ const c = racine.querySelector("input[name='" + el.name + "']:checked"); return c ? c.value : ""; }
    return el.value;
  }

  function suivante(etat, i){
    const sections = etat.structure.sections, s = sections[i];
    let cible = s.apres || SUIVANTE;
    const b = s.branche;
    if(b){ const cle = cleRegle(b.regles, valeurDe(etat.racine, b.champ)); if(cle !== null) cible = b.regles[cle]; }
    if(cible === SOUMETTRE) return -1;
    const j = cible === SUIVANTE ? i + 1 : (etat.rang[cible] ?? i + 1);
    return j >= sections.length ? -1 : j;
  }

  function parcours(etat){
    const vues = []; let i = 0;
    while(i >= 0 && i < etat.structure.sections.length && vues.length <= etat.structure.sections.length){ vues.push(i); i = suivante(etat, i); }
    return vues;
  }

  // Champs à valider / envoyer : ceux des sections traversées + ceux qui ne sont dans aucune section.
  function champsActifs(table, racine){
    const etat = racine && racine._gyfc;
    if(!actif(table) || !etat) return table.champs;
    const slugs = new Set(parcours(etat).flatMap(i => etat.structure.sections[i].champs));
    return table.champs.filter(c => slugs.has(c.slug) || !etat.dansSection.has(c.slug));
  }

  function validerSection(etat, i){
    let ok = true;
    for(const slug of etat.structure.sections[i].champs){
      const c = etat.table.champs.find(x => x.slug === slug); if(!c) continue;
      const el = etat.racine.querySelector("#champ_" + slug);
      if(!el) continue;
      el.classList.remove("champ-invalide");
      if(!c.obligatoire || c.type === "formule" || c.type === "constante" || c.type === "horodatage" || c.type === "id_client") continue;
      let vide;
      if(c.type === "booleen" || c.type === "switch") vide = false;
      else if(el.files) vide = el.files.length === 0;
      else if(el.selectedOptions && el.multiple) vide = el.selectedOptions.length === 0;
      else vide = (el.value || "").trim() === "";
      if(vide){ el.classList.add("champ-invalide"); ok = false; }
    }
    return ok;
  }

  function afficher(etat, i){
    etat.courant = i;
    etat.panneaux.forEach((p, k) => { p.style.display = k === i ? "" : "none"; });
    const final = suivante(etat, i) === -1;
    etat.btnRetour.style.display = etat.historique.length ? "" : "none";
    etat.btnSuivant.style.display = final ? "none" : "";
    if(etat.actions) etat.actions.style.display = final ? "" : "none";   // « Ajouter » seulement à la dernière section du parcours
    etat.indic.textContent = T("indicateur", {v1: etat.historique.length + 1});
  }

  // Regroupe les .champ-item déjà rendus de `racine` en sections. `actions` : conteneur du bouton d'envoi (masqué tant que le parcours continue).
  function monter(table, racine, actions){
    if(!actif(table) || !racine) return false;
    if(racine._gyfc){ return true; }
    const structure = table.structure_formulaire;
    const etat = {table, structure, racine, actions: actions || null, rang: {}, historique: [], courant: 0, panneaux: [], dansSection: new Set()};
    racine._gyfc = etat;
    structure.sections.forEach((s, i) => { etat.rang[s.id] = i; s.champs.forEach(slug => etat.dansSection.add(slug)); });
    const items = {};
    racine.querySelectorAll(".champ-item[data-champ-slug]").forEach(el => { items[el.dataset.champSlug] = el; });
    const pere = racine;
    structure.sections.forEach(s => {
      const panneau = document.createElement("div");
      panneau.className = "form-section";
      panneau.dataset.sectionId = s.id;   // apparence propre à la section (formulaire_theme.js : appliquerDetails)
      if(s.titre || s.description){
        const tete = document.createElement("div"); tete.className = "form-section-tete";
        if(s.titre){ const h = document.createElement("h3"); h.textContent = s.titre; tete.appendChild(h); }
        if(s.description){ const p = document.createElement("p"); p.textContent = s.description; tete.appendChild(p); }
        panneau.appendChild(tete);
      }
      s.champs.forEach(slug => { if(items[slug]) panneau.appendChild(items[slug]); });
      etat.panneaux.push(panneau);
      pere.appendChild(panneau);
    });
    const nav = document.createElement("div"); nav.className = "form-section-nav";
    etat.indic = document.createElement("span"); etat.indic.className = "form-section-indic";
    etat.btnRetour = document.createElement("button"); etat.btnRetour.type = "button"; etat.btnRetour.textContent = T("retour");
    etat.btnSuivant = document.createElement("button"); etat.btnSuivant.type = "button"; etat.btnSuivant.className = "principal"; etat.btnSuivant.textContent = T("suivant");
    etat.btnRetour.onclick = () => { if(etat.historique.length) afficher(etat, etat.historique.pop()); };
    etat.btnSuivant.onclick = () => {
      if(!validerSection(etat, etat.courant)) return;
      const j = suivante(etat, etat.courant);
      if(j < 0) return;
      etat.historique.push(etat.courant); afficher(etat, j);
    };
    nav.appendChild(etat.btnRetour); nav.appendChild(etat.indic); nav.appendChild(etat.btnSuivant);
    pere.appendChild(nav);
    const maj = () => afficher(etat, etat.courant);
    racine.addEventListener("change", maj); racine.addEventListener("input", maj);
    afficher(etat, 0);
    return true;
  }

  window.gyFormCondIndex = {actif, monter, champsActifs};
})();
