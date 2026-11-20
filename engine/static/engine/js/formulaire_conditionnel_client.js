// Formulaire conditionnel côté client : sections (une « page » chacune) et renvois selon les réponses, façon Google Forms.
// La table ne change pas : les champs des sections non traversées ne sont simplement pas envoyés (cellules vides).
// Le parcours est calculé ici pour l'affichage ; le serveur le recalcule à l'enregistrement (engine/formulaire_conditionnel.py).
(function(){
  const K = "engine/static/engine/js/formulaire_conditionnel_client.js:";
  const T = (cle, vars) => window.tj ? window.tj(K + cle, vars) : cle;
  const SOUMETTRE = "soumettre", SUIVANTE = "suivante";
  let etat = null;   // {table, structure, rang, historique:[index de section], courant}

  function actif(table){ return !!(table && table.structure_formulaire && table.structure_formulaire.sections && table.structure_formulaire.sections.length); }

  function canon(v){
    if(Array.isArray(v)) v = v.length ? v[0] : "";
    if(v === true) return "true";
    if(v === false) return "false";
    return v == null ? "" : String(v).trim();
  }
  function cleRegle(regles, v){
    v = canon(v);
    if(v in regles) return v;
    if(v === "1" && "true" in regles) return "true";
    if(v === "0" && "false" in regles) return "false";
    return null;
  }
  const champDe = (slug) => etat.table.champs.find(c => c.slug === slug);
  const valeurDe = (slug) => { const c = champDe(slug); return c ? valeurChamp(c) : ""; };

  // Section vers laquelle mène la section i d'après les réponses actuelles : index, ou -1 = envoyer le formulaire.
  function suivante(i){
    const sections = etat.structure.sections, s = sections[i];
    let cible = s.apres || SUIVANTE;
    const b = s.branche;
    if(b){ const cle = cleRegle(b.regles, valeurDe(b.champ)); if(cle !== null) cible = b.regles[cle]; }
    if(cible === SOUMETTRE) return -1;
    const j = cible === SUIVANTE ? i + 1 : (etat.rang[cible] ?? i + 1);
    return j >= sections.length ? -1 : j;
  }

  // Sections traversées d'après les réponses actuelles (même algorithme que sections_parcourues côté serveur).
  function parcours(){
    const vues = []; let i = 0;
    while(i >= 0 && i < etat.structure.sections.length && vues.length <= etat.structure.sections.length){ vues.push(i); i = suivante(i); }
    return vues;
  }

  // Champs à valider et à envoyer : ceux des sections traversées (tous les champs si le formulaire n'est pas conditionnel).
  function champsActifs(table){
    if(!actif(table) || !etat || etat.table !== table) return table.champs;
    const slugs = new Set(parcours().flatMap(i => etat.structure.sections[i].champs));
    return table.champs.filter(c => slugs.has(c.slug) || !etat.dansSection.has(c.slug));
  }

  function validerSection(i){
    let ok = true;
    for(const slug of etat.structure.sections[i].champs){
      const c = champDe(slug); if(!c) continue;
      const msg = document.getElementById('err-' + c.slug), el = document.getElementById('champ-' + c.slug);
      if(msg) msg.textContent = ''; if(el) el.classList.remove('erreur');
      if(c.obligatoire){
        const v = valeurChamp(c);
        if(v === null || v === '' || (Array.isArray(v) && v.length === 0)){
          if(msg) msg.textContent = tj("engine/static/engine/js/client_1.js:590:47"); if(el) el.classList.add('erreur'); ok = false;
        }
      }
    }
    return ok;
  }

  function afficher(i, defiler = true){
    etat.courant = i;
    const sections = etat.structure.sections;
    etat.panneaux.forEach((p, k) => { p.style.display = k === i ? '' : 'none'; });
    const suiv = suivante(i), final = suiv === -1;
    etat.btnRetour.style.display = etat.historique.length ? '' : 'none';
    etat.btnSuivant.style.display = final ? 'none' : '';
    const actions = document.getElementById('form-actions');
    if(actions) actions.style.display = final ? '' : 'none';   // « Envoyer » seulement à la dernière section du parcours
    etat.indic.textContent = T('indicateur', {v1: etat.historique.length + 1});
    if(defiler){ const zone = document.getElementById('zone-formulaire'); if(zone && zone.scrollIntoView) zone.scrollIntoView({block: 'start'}); }
  }

  // Une réponse qui change la suite du parcours met à jour les boutons (Suivant ↔ Envoyer).
  function majNavigation(){ if(etat) afficher(etat.courant, false); }

  // `creerGroupe(champ)` : fabrique du bloc libellé + widget + message d'erreur d'un champ (client_1.js).
  function construire(table, form, creerGroupe){
    const structure = table.structure_formulaire;
    etat = {table, structure, rang: {}, historique: [], courant: 0, panneaux: [], dansSection: new Set()};
    structure.sections.forEach((s, i) => { etat.rang[s.id] = i; s.champs.forEach(slug => etat.dansSection.add(slug)); });
    form.innerHTML = '';
    structure.sections.forEach((s, i) => {
      const panneau = document.createElement('div');
      panneau.className = 'form-section';
      panneau.dataset.sectionId = s.id;   // apparence propre à la section (formulaire_theme.js : appliquerDetails)
      if(s.titre || s.description){
        const tete = document.createElement('div'); tete.className = 'form-section-tete';
        if(s.titre){ const h = document.createElement('h3'); h.textContent = s.titre; tete.appendChild(h); }
        if(s.description){ const p = document.createElement('p'); p.textContent = s.description; tete.appendChild(p); }
        panneau.appendChild(tete);
      }
      s.champs.forEach(slug => { const c = champDe(slug); if(c && c.type !== 'horodatage' && c.type !== 'id_client') panneau.appendChild(creerGroupe(c)); });
      etat.panneaux.push(panneau);
      form.appendChild(panneau);
    });
    const nav = document.createElement('div'); nav.className = 'form-section-nav';
    etat.indic = document.createElement('span'); etat.indic.className = 'form-section-indic';
    etat.btnRetour = document.createElement('button'); etat.btnRetour.type = 'button'; etat.btnRetour.textContent = T('retour');
    etat.btnSuivant = document.createElement('button'); etat.btnSuivant.type = 'button'; etat.btnSuivant.className = 'principal'; etat.btnSuivant.textContent = T('suivant');
    etat.btnRetour.onclick = () => { if(etat.historique.length) afficher(etat.historique.pop()); };
    etat.btnSuivant.onclick = () => {
      if(!validerSection(etat.courant)) return;
      const j = suivante(etat.courant);
      if(j < 0) return;
      etat.historique.push(etat.courant); afficher(j);
    };
    nav.appendChild(etat.btnRetour); nav.appendChild(etat.indic); nav.appendChild(etat.btnSuivant);
    form.appendChild(nav);
    form.addEventListener('change', majNavigation);
    form.addEventListener('input', majNavigation);
    afficher(0);
  }

  window.gyFormCond = {actif, construire, champsActifs};
})();
