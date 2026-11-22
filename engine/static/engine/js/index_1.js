const TYPES_CHAMPS = [
  "texte","texte_long","nombre","decimal","monnaie","pourcentage","note",
  "date","heure","datetime","horodatage","duree","booleen","switch","barre_progression","relation","colonne",
  "email","url","telephone","couleur","choix_unique","choix_multiple","fichier","image","son","video",
  "vecteur","matrice","geolocalisation","json","formule","constante","id_client",
];
const TYPES_NUM_JS = (window.TYPES_NUMERIQUES || "nombre,decimal,monnaie,pourcentage,barre_progression,note").split(",");  // source : formules_science.py
// Champs dont le champ "options" (réutilisé) sert à autre chose que des
// choix séparés par virgules — matrice/vecteur y stockent la FORME
// attendue (ex: "10" ou "3,4") pour le module d'entraînement IA.
const TYPES_AVEC_OPTIONS = ["choix_unique","choix_multiple","vecteur","matrice","constante"];
const TYPES_TENSEUR = ["vecteur","matrice"];
const TYPES_FICHIER = ["fichier","image","son","video"];
// `booleen` et `switch` partagent le même comportement de validation
// (une case à cocher/un toggle est toujours considéré comme "rempli") et le
// même format de valeur côté payload ("1" / "0").
const TYPES_BOOLEEN = ["booleen","switch"];
function getCookie(nom){
  const valeur = document.cookie.split("; ").find(r => r.startsWith(nom+"="));
  return valeur ? decodeURIComponent(valeur.split("=")[1]) : null;
}
function notifier(message, type="succes"){
  const conteneur = document.getElementById("toast-conteneur");
  const toast = document.createElement("div");
  toast.className = "toast " + type;
  toast.textContent = message;
  conteneur.appendChild(toast);
  setTimeout(() => toast.remove(), 4500);
}
function notifierSucces(message){ notifier(message, "succes"); }
function notifierErreur(message){ notifier(message, "erreur"); }
// Copie dans le presse-papiers avec repli automatique : navigator.clipboard
// exige un contexte sécurisé (HTTPS ou localhost) — hors de ce contexte
// (ex. accès via IP locale en HTTP), l'API est absente ou rejette toujours,
// et TOUS les boutons "Copier" du site échouaient silencieusement. On
// retombe donc sur document.execCommand("copy") via un textarea temporaire.
async function copierPressePapiers(texte){
  if(navigator.clipboard && window.isSecureContext){
    try { await navigator.clipboard.writeText(texte); return true; } catch(e) { /* tente le repli */ }
  }
  try {
    const tmp = document.createElement("textarea");
    tmp.value = texte;
    tmp.style.position = "fixed"; tmp.style.opacity = "0"; tmp.style.top = "0"; tmp.style.left = "0";
    document.body.appendChild(tmp);
    tmp.focus(); tmp.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(tmp);
    return ok;
  } catch(e) { return false; }
}
// Filet de sécurité global : une erreur JS inattendue (dans un handler async
// non catché, par ex.) ne doit plus jamais se traduire par un clic qui "ne
// fait rien" — on l'affiche au moins en toast pour que ce soit visible.
window.addEventListener("unhandledrejection", (ev) => {
  console.error(tj("engine/static/engine/js/index_1.js:56:18"), ev.reason);
  notifierErreur(tj("engine/static/engine/js/index_1.js:57:19"));
});
window.addEventListener("error", (ev) => {
  console.error(tj("engine/static/engine/js/index_1.js:60:18"), ev.error || ev.message);
});
async function api(url, options={}){
  options.headers = Object.assign({"X-CSRFToken": getCookie("gy_csrftoken")}, options.headers||{});
  if(options.body && !(options.body instanceof FormData)){
    options.headers["Content-Type"] = "application/json";
  }
  let r;
  try {
    r = await fetch(url, options);
  } catch (e) {
    // Annulation explicite via AbortController : on reste silencieux,
    // c'est l'appelant qui gère (bouton « Annuler » de la génération).
    if (e && e.name === "AbortError") throw e;
    notifierErreur(tj("engine/static/engine/js/index_1.js:74:21"));
    throw e;
  }
  if(!r.ok){
    const donnees = await r.json().catch(()=>({erreur:r.statusText}));
    const message = donnees.erreur || r.statusText || tj("engine/static/engine/js/index_1.js:79:56");
    notifierErreur(message);
    const err = new Error(message);
    if(donnees.champs) err.champsInvalides = donnees.champs;
    if(donnees.peut_etendre) err.peutEtendre = true;
    if(donnees.plafond_colonnes != null) err.plafondColonnes = donnees.plafond_colonnes;
    if(donnees.code) err.code = donnees.code;   // ex. « non_annulable », « modifie_depuis » : l'appelant propose de confirmer
    throw err;
  }
  return r.json();
}
// ── Attente asynchrone (Celery) ─────────────────────────────────────────
// Depuis le passage du back-end en tâches Celery, les endpoints qui
// déclenchent un appel IA / une génération / un entraînement répondent
// IMMÉDIATEMENT avec un statut transitoire ("en_cours") : le travail réel se
// termine plus tard sur un worker séparé. Ces deux fonctions font le lien
// côté front : on interroge à intervalle régulier l'endpoint de statut
// dédié jusqu'à ce que le serveur indique "fini: true", puis on retourne
// l'objet final (demande / document), exactement comme le faisaient les
// anciens appels synchrones.
// `signal` (AbortController) permet d'annuler l'attente comme avant.
async function _attendreFin(urlStatut, cleObjet, {signal, intervalle=1200} = {}){
  while(true){
    if(signal && signal.aborted){
      const err = new Error(tj("engine/static/engine/js/index_1.js:103:30"));
      err.name = "AbortError";
      throw err;
    }
    const data = await api(urlStatut, {signal});
    if(data.fini) return data[cleObjet];
    await new Promise((resoudre, rejeter) => {
      const t = setTimeout(resoudre, intervalle);
      if(signal){
        signal.addEventListener("abort", () => {
          clearTimeout(t);
          const err = new Error(tj("engine/static/engine/js/index_1.js:114:34"));
          err.name = "AbortError";
          rejeter(err);
        }, {once:true});
      }
    });
  }
}
function attendreFinDemande(demandeId, options={}){
  return _attendreFin(`/api/demandes/${demandeId}/statut/`, "demande", options);
}
function attendreFinDocument(docId, options={}){
  return _attendreFin(`/api/documents/${docId}/statut/`, "document", options);
}
function attendreFinGenerationFormule(generationId, options={}){
  return _attendreFin(`/api/formules-calcul/generer/${generationId}/statut/`, "generation", options);
}

// ── Modal Carte d'agent (thème, colonnes affichées, éléments actifs, couleurs) ─
// Disposition FIGEE et calculee automatiquement cote serveur (voir
// reports.generer_pdf_cartes_agents) : ici on ne configure QUE la
// selection/ordre des colonnes, les toggles logo/QR/signature/cachet par
// face et les couleurs — plus aucune position libre (x/y) a saisir.
let _carteTable = null, _carteConfig = {colonnes: [], couleurs: {}}, _carteFace = "recto";
let _carteAgents = [], _carteAgentsSelectionnes = [];  // ordre de selection = ordre d'impression
const CARTE_MAX_AGENTS = 10;
// Affiche, à côté de chaque <input type="file"> de la modale carte, un
// rappel visuel (miniature d'image + lien "Ouvrir") du fichier actuellement
// enregistré côté serveur pour cette table — sinon rien n'indique qu'un
// thème/signature/cachet/QR a déjà été configuré lors d'une réouverture.
function _majApercusFichiersCarte(table){
  const champs = [
    ["carte-theme-recto-apercu", table.theme_carte_recto_url, tj("engine/static/engine/js/index_1.js:146:64")],
    ["carte-theme-verso-apercu", table.theme_carte_verso_url, tj("engine/static/engine/js/index_1.js:147:64")],
    ["carte-signature-apercu", table.carte_signature_url, tj("engine/static/engine/js/index_1.js:lbl_signature_actuelle")],
    ["carte-cachet-apercu", table.carte_cachet_url, tj("engine/static/engine/js/index_1.js:lbl_cachet_actuel")],
    ["carte-qrcode-apercu", table.carte_qrcode_url, tj("engine/static/engine/js/index_1.js:lbl_qrcode_actuel")],
  ];
  champs.forEach(([id, url, titre]) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = url
      ? `<span style="display:inline-flex;align-items:center;gap:5px;font-size:10px;color:var(--muted)">
          <img src="${url}" title="${titre}" style="height:28px;max-width:44px;object-fit:contain;border:1px solid var(--border);border-radius:3px;background:var(--fond-clair,#fff)">
          <a href="${url}" target="_blank" rel="noopener" class="btn petit">${tj("engine/static/engine/js/index_1.js:158:77")}</a>
        </span>`
      : `<span style="font-size:10px;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:160:59")}</span>`;
  });
}
async function ouvrirModalCarte(tableInitiale){
  // On re-fetch systematiquement la table depuis le serveur a l'ouverture,
  // plutot que de faire confiance a l'objet JS recu en parametre (capture
  // de fermeture liee au dernier rendu de la barre d'actions, qui peut ne
  // plus correspondre a l'etat reellement enregistre en base si le bouton
  // n'a pas ete re-cree entre-temps) : ainsi la modale reflete toujours,
  // a coup sur, ce qui est vraiment persiste cote serveur.
  let table = tableInitiale;
  try {
    const data = await api("/api/tables/");
    tables = data.tables;
    const fraiche = tables.find(t => t.id === tableInitiale.id);
    if (fraiche) table = fraiche;
  } catch(e) { /* si le re-fetch echoue, on retombe sur tableInitiale */ }
  _carteTable = table;
  const cfg = table.config_carte || {};
  _carteConfig = {
    colonnes: cfg.colonnes || [],
    logo_recto: !!cfg.logo_recto, logo_verso: !!cfg.logo_verso,
    qrcode_recto: !!cfg.qrcode_recto, qrcode_verso: !!cfg.qrcode_verso,
    signature_recto: !!cfg.signature_recto, signature_verso: !!cfg.signature_verso,
    cachet_recto: !!cfg.cachet_recto, cachet_verso: !!cfg.cachet_verso,
    couleurs: cfg.couleurs || {},
    taille_police: cfg.taille_police || 8,
    faces: cfg.faces === "recto" ? "recto" : "recto_verso",   // recto seul ou recto + verso
    sortie: cfg.sortie === "images" ? "images" : "pdf",       // PDF (planche) ou images PNG individuelles
    police: cfg.police || "duplex",
    qrcode_source: cfg.qrcode_source || "auto", qrcode_mode: cfg.qrcode_mode || "resume",
    qrcode_champ: cfg.qrcode_champ || "", qrcode_colonnes: cfg.qrcode_colonnes || [],
  };
  _carteFace = "recto";
  _carteAgentsSelectionnes = [];
  document.getElementById("carte-signature-nom").value = table.carte_signature_nom || "";
  document.getElementById("carte-matricule-prefixe").value = table.matricule_prefixe || "";
  // Les <input type="file"> ne peuvent jamais être pré-remplis par sécurité
  // navigateur : sans ceci, un thème/signature/cachet/QR déjà enregistré
  // semble avoir disparu à chaque réouverture de la modale — on affiche donc
  // un aperçu (miniature + lien) du fichier actuellement en base à côté de
  // chaque champ, mis à jour aussi après un enregistrement (_sauvegarderConfigCarte).
  _majApercusFichiersCarte(table);
  document.getElementById(_carteConfig.qrcode_source === "upload" ? "carte-qr-source-upload" : "carte-qr-source-auto").checked = true;
  document.getElementById("carte-qr-mode").value = _carteConfig.qrcode_mode;
  const optsChamps = (table.champs || []).filter(c => !["image","audio","video"].includes(c.type));
  document.getElementById("carte-qr-champ").innerHTML = optsChamps.map(c => `<option value="${c.slug}">${escapeHtml(c.nom)}</option>`).join("");
  document.getElementById("carte-qr-champ").value = _carteConfig.qrcode_champ;
  document.getElementById("carte-qr-colonnes").innerHTML = optsChamps.map(c => `<option value="${c.slug}" ${_carteConfig.qrcode_colonnes.includes(c.slug) ? "selected" : ""}>${escapeHtml(c.nom)}</option>`).join("");
  document.querySelectorAll('input[name="qrcode-source"]').forEach(r => r.onchange = _majBlocsQr);
  document.getElementById("carte-qr-mode").onchange = _majBlocsQr;
  _majBlocsQr();
  document.getElementById("carte-couleur-texte").value = _carteConfig.couleurs.texte || gyCouleur("--fond-releve","#1a1a1a");
  document.getElementById("carte-couleur-bordure").value = _carteConfig.couleurs.bordure || gyCouleur("--texte-discret","#999999");
  document.getElementById("carte-couleur-separateur").value = _carteConfig.couleurs.separateur || gyCouleur("--texte-clair","#dddddd");
  document.getElementById("carte-taille-police").value = _carteConfig.taille_police;
  document.getElementById("carte-police").value = _carteConfig.police;
  document.getElementById("carte-faces").value = _carteConfig.faces;
  document.getElementById("carte-sortie").value = _carteConfig.sortie;
  document.getElementById("carte-faces").onchange = () => { _lireCarteFace(); _carteConfig.faces = document.getElementById("carte-faces").value; _majFacesCarte(); _rendreCarteFace(); };
  _majFacesCarte();
  document.getElementById("modal-carte-fond").style.display = "flex";
  _rendreCarteFace();
  try {
    const data = await api(`/api/tables/${table.id}/lignes/`);
    _carteAgents = data.lignes || [];
  } catch(e) { _carteAgents = []; }
  _rendreCarteGrille();
}
// « Recto seulement » : le verso n'est ni généré ni configurable (onglet et thème verso masqués).
function _majFacesCarte(){
  const rectoSeul = _carteConfig.faces === "recto";
  document.getElementById("carte-face-verso").style.display = rectoSeul ? "none" : "";
  document.getElementById("carte-theme-verso-bloc").style.display = rectoSeul ? "none" : "";
  if(rectoSeul) _carteFace = "recto";
}
function _rendreCarteFace(){
  document.getElementById("carte-face-recto").classList.toggle("actif", _carteFace === "recto");
  document.getElementById("carte-face-verso").classList.toggle("actif", _carteFace === "verso");
  document.getElementById("carte-toggle-logo").checked = !!_carteConfig[`logo_${_carteFace}`];
  document.getElementById("carte-toggle-qrcode").checked = !!_carteConfig[`qrcode_${_carteFace}`];
  document.getElementById("carte-toggle-signature").checked = !!_carteConfig[`signature_${_carteFace}`];
  document.getElementById("carte-toggle-cachet").checked = !!_carteConfig[`cachet_${_carteFace}`];
  // Les colonnes de donnees (config_carte.colonnes) ne s'appliquent qu'au
  // RECTO cote generation PDF (voir reports.generer_pdf_cartes_agents) : sur
  // l'onglet verso, les en-tetes restent visibles (structure de table
  // complete) mais grisees/non cliquables — la selection des AGENTS a
  // imprimer, elle, reste active sur les deux onglets.
  _rendreCarteGrille();
}
function _majBlocsQr(){
  const auto = document.getElementById("carte-qr-source-auto").checked;
  const mode = document.getElementById("carte-qr-mode").value;
  document.getElementById("carte-qr-mode").disabled = !auto;
  document.getElementById("carte-qr-champ-bloc").style.display = (auto && mode === "champ") ? "" : "none";
  document.getElementById("carte-qr-colonnes-bloc").style.display = (auto && mode === "resume") ? "" : "none";
}
function _lireCarteFace(){
  _carteConfig[`logo_${_carteFace}`] = document.getElementById("carte-toggle-logo").checked;
  _carteConfig[`qrcode_${_carteFace}`] = document.getElementById("carte-toggle-qrcode").checked;
  _carteConfig[`signature_${_carteFace}`] = document.getElementById("carte-toggle-signature").checked;
  _carteConfig[`cachet_${_carteFace}`] = document.getElementById("carte-toggle-cachet").checked;
}
function _majCarteCompteur(){
  document.getElementById("carte-agents-compteur").textContent =
    tj("engine/static/engine/js/index_1.js:252:6", {v1: (_carteConfig.colonnes || []).length, v2: _carteAgentsSelectionnes.length, v3: CARTE_MAX_AGENTS});
}
// ── Grille reelle (vraie table, comme le moteur l'exige) : en-tetes de
// colonnes cliquables = colonnes affichees sur la carte (badge = ordre de
// priorite) ; 1ere cellule de chaque ligne cliquable = agent inclus dans
// l'impression (badge = ordre d'impression). Remplace les deux anciennes
// listes a cases a cocher, ou la structure de la table n'etait pas visible. ─
function _rendreCarteGrille(){
  const grilleEl = document.getElementById("carte-grille");
  const champs = _carteTable.champs || [];
  const colonnes = _carteConfig.colonnes || [];
  const surVerso = _carteFace === "verso";
  grilleEl.className = surVerso ? "carte-verso" : "";

  if (!_carteAgents.length){
    grilleEl.innerHTML = `<tr><td style="padding:14px;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:267:76")}</td></tr>`;
    _majCarteCompteur();
    return;
  }

  let html = `<thead><tr><th class="selcible-entete" style="cursor:default">#</th><th class="selcible-entete" style="cursor:default">${tj("engine/static/engine/js/index_1.js:lbl_matricule")}</th>`;
  champs.forEach(c => {
    const compatible = !["image","audio","video"].includes(c.type);
    const actif = compatible && colonnes.includes(c.slug);
    const badge = actif ? `<span class="carte-badge">${colonnes.indexOf(c.slug) + 1}</span>` : "";
    html += `<th class="selcible-entete${compatible ? "" : " selcible-incompatible"}${actif ? " selcible-actif" : ""}" data-slug="${c.slug}">${escapeHtml(c.nom)}${badge}</th>`;
  });
  html += `</tr></thead><tbody>`;
  _carteAgents.forEach((l, ri) => {
    const posImpression = _carteAgentsSelectionnes.indexOf(l._id);
    const inclus = posImpression !== -1;
    html += `<tr data-id="${l._id}" class="${inclus ? "carte-ligne-active" : ""}"><td>${inclus ? `<span class="carte-badge">${posImpression + 1}</span>` : ri + 1}</td><td class="selcible-cellule">${escapeHtml(l.matricule || "—")}</td>`;
    champs.forEach(c => {
      let v = l[c.slug];
      if (v === null || v === undefined || v === "") v = (c.type === "image" ? "🖼" : "—");
      else if (typeof v === "object") v = JSON.stringify(v);
      html += `<td class="selcible-cellule">${escapeHtml(String(v).slice(0, 40))}</td>`;
    });
    html += `</tr>`;
  });
  html += `</tbody>`;
  grilleEl.innerHTML = html;

  if (!surVerso){
    grilleEl.querySelectorAll("thead th[data-slug]").forEach(th => {
      if (th.classList.contains("selcible-incompatible")) return;
      th.onclick = () => {
        const slug = th.dataset.slug;
        let cols = [..._carteConfig.colonnes];
        if (cols.includes(slug)) cols = cols.filter(s => s !== slug);
        else cols.push(slug);  // ajoutee en fin -> priorite = ordre de clic
        _carteConfig.colonnes = cols;
        _rendreCarteGrille();
      };
    });
  }
  grilleEl.querySelectorAll("tbody tr[data-id]").forEach(tr => {
    tr.querySelector("td:first-child").onclick = () => {
      const id = parseInt(tr.dataset.id, 10);
      if (_carteAgentsSelectionnes.includes(id)){
        _carteAgentsSelectionnes = _carteAgentsSelectionnes.filter(v => v !== id);
      } else {
        if (_carteAgentsSelectionnes.length >= CARTE_MAX_AGENTS){
          notifierErreur(tj("engine/static/engine/js/index_1.js:315:27"));
          return;
        }
        _carteAgentsSelectionnes.push(id);
      }
      _rendreCarteGrille();
    };
  });
  _majCarteCompteur();
}
document.getElementById("carte-face-recto").onclick = () => { _lireCarteFace(); _carteFace = "recto"; _rendreCarteFace(); };
document.getElementById("carte-face-verso").onclick = () => { _lireCarteFace(); _carteFace = "verso"; _rendreCarteFace(); };
document.getElementById("btn-annuler-carte").onclick = () => { document.getElementById("modal-carte-fond").style.display = "none"; };
// Rassemble l'etat courant du formulaire (jamais persiste tant que cette
// fonction n'est pas appelee) et l'envoie au serveur. Utilisee par le bouton
// "Enregistrer" ET par "Generer" — avant, "Generer" ouvrait directement le
// PDF sans sauvegarder _carteConfig, qui n'existe qu'en memoire JS et est
// reinitialise depuis table.config_carte a chaque ouverture de la modale :
// toute colonne/couleur/toggle modifie sans passer par "Enregistrer" etait
// donc perdu (PDF genere avec l'ancienne config, ou config perdue a la
// fermeture de la modale). On sauvegarde desormais systematiquement avant
// de generer, pour que rien ne reste volatile.
async function _sauvegarderConfigCarte(){
  _lireCarteFace();
  _carteConfig.couleurs = {
    texte: document.getElementById("carte-couleur-texte").value,
    bordure: document.getElementById("carte-couleur-bordure").value,
    separateur: document.getElementById("carte-couleur-separateur").value,
  };
  const taillePolice = parseFloat(document.getElementById("carte-taille-police").value);
  _carteConfig.taille_police = Number.isFinite(taillePolice) ? Math.min(14, Math.max(5, taillePolice)) : 8;
  _carteConfig.police = document.getElementById("carte-police").value;
  _carteConfig.faces = document.getElementById("carte-faces").value === "recto" ? "recto" : "recto_verso";
  _carteConfig.sortie = document.getElementById("carte-sortie").value === "images" ? "images" : "pdf";
  _carteConfig.qrcode_source = document.getElementById("carte-qr-source-upload").checked ? "upload" : "auto";
  _carteConfig.qrcode_mode = document.getElementById("carte-qr-mode").value;
  _carteConfig.qrcode_champ = document.getElementById("carte-qr-champ").value;
  _carteConfig.qrcode_colonnes = [...document.getElementById("carte-qr-colonnes").selectedOptions].map(o => o.value);
  const fd = new FormData();
  const fr = document.getElementById("carte-theme-recto").files[0];
  const fv = document.getElementById("carte-theme-verso").files[0];
  const fs = document.getElementById("carte-signature-fichier").files[0];
  const fc = document.getElementById("carte-cachet-fichier").files[0];
  const fq = document.getElementById("carte-qrcode-fichier").files[0];
  if (fr) fd.append("theme_recto", fr);
  if (fv) fd.append("theme_verso", fv);
  if (fs) fd.append("signature", fs);
  if (fc) fd.append("cachet", fc);
  if (fq) fd.append("qrcode", fq);
  fd.append("signature_nom", document.getElementById("carte-signature-nom").value || "");
  fd.append("matricule_prefixe", document.getElementById("carte-matricule-prefixe").value || "");
  fd.append("config_carte", JSON.stringify(_carteConfig));
  const res = await api(`/api/tables/${_carteTable.id}/config-carte/`, {method:"POST", body: fd});
  _carteTable.config_carte = res.config_carte;
  _carteTable.carte_signature_nom = res.carte_signature_nom;
  _carteTable.matricule_prefixe = res.matricule_prefixe;
  // res = table serialisee complete (_serialiser_table) : on recupere aussi
  // les URLs des fichiers (theme/signature/cachet/QR) pour que l'apercu
  // reflete immediatement ce qui vient d'etre enregistre, et on vide les
  // <input type="file"> deja envoyes pour eviter de les renvoyer par erreur
  // au prochain enregistrement.
  _carteTable.theme_carte_recto_url = res.theme_carte_recto_url;
  _carteTable.theme_carte_verso_url = res.theme_carte_verso_url;
  _carteTable.carte_signature_url = res.carte_signature_url;
  _carteTable.carte_cachet_url = res.carte_cachet_url;
  _carteTable.carte_qrcode_url = res.carte_qrcode_url;
  _majApercusFichiersCarte(_carteTable);
  ["carte-theme-recto","carte-theme-verso","carte-signature-fichier","carte-cachet-fichier","carte-qrcode-fichier"]
    .forEach(id => { document.getElementById(id).value = ""; });
  return res;
}
document.getElementById("btn-generer-carte").onclick = async () => {
  if (_carteAgentsSelectionnes.length === 0){
    notifierErreur(tj("engine/static/engine/js/index_1.js:386:21"));
    return;
  }
  const btn = document.getElementById("btn-generer-carte");
  btn.disabled = true;
  try {
    // On sauvegarde toujours la config avant de générer, pour que le PDF
    // reflète exactement ce qui est affiché dans la modale — plus de
    // configuration volatile perdue faute d'avoir cliqué "Enregistrer".
    await _sauvegarderConfigCarte();
    await gyTelecharger(`/api/tables/${_carteTable.id}/generer-cartes/?ids=${_carteAgentsSelectionnes.join(",")}`, tj("engine/static/engine/js/index_1.js:396:117", {v1: _carteTable.slug}),
      {succes: tj("engine/static/engine/js/index_1.js:397:17")});
  } catch(e) {
    notifierErreur(tj("engine/static/engine/js/index_1.js:399:21"));
  } finally {
    btn.disabled = false;
  }
};
// "Tout sélectionner" : contourne la limite de 10 agents/PDF — le serveur
// decoupe TOUTE la table par lots de 10 (dernier lot incomplet inclus) et
// renvoie un .zip avec un PDF par lot (voir api_generer_cartes_zip).
document.getElementById("btn-carte-tout-generer").onclick = async () => {
  const btn = document.getElementById("btn-carte-tout-generer");
  btn.disabled = true;
  try {
    await _sauvegarderConfigCarte();
    await gyTelecharger(`/api/tables/${_carteTable.id}/generer-cartes-zip/`, tj("engine/static/engine/js/index_1.js:412:79", {v1: _carteTable.slug}),
      {succes: tj("engine/static/engine/js/index_1.js:413:17")});
  } catch(e) {
    notifierErreur(tj("engine/static/engine/js/index_1.js:415:21"));
  } finally {
    btn.disabled = false;
  }
};
document.getElementById("btn-enregistrer-carte").onclick = async () => {
  try {
    await _sauvegarderConfigCarte();
    notifierSucces(tj("engine/static/engine/js/index_1.js:423:21"));
    rafraichirTables();
  } catch(e) {}
};

// Contexte de l'accueil : dernière table ouverte et sa vue, mémorisées dans le navigateur (jamais bloquant). Au retour sur l'accueil
// (lien « Agent GY », bouton retour, nouvel onglet), on rouvre cette table au lieu de la première de la liste.
const CLE_CONTEXTE_ACCUEIL = "gy_accueil_contexte";
function memoriserContexteAccueil(){
  try{
    if(!tableActive || !["table", "dashboard", "documents", "calcul"].includes(modeVue)) return;
    localStorage.setItem(CLE_CONTEXTE_ACCUEIL, JSON.stringify({slug: tableActive, vue: modeVue}));
  }catch(e){ /* stockage indisponible */ }
}
function lireContexteAccueil(){
  try{
    const m = JSON.parse(localStorage.getItem(CLE_CONTEXTE_ACCUEIL) || "null");
    return m && typeof m.slug === "string" ? m : null;
  }catch(e){ return null; }
}
let tables = [];
let tableActive = null;
let modeVue = "vide";
let scriptsEnAttente = {};
// Helper : affiche les meta (lignes/col) dans le badge de la barre d'actions
// Gestion de la Sidebar responsive
const btnToggleSidebar = document.getElementById("btn-toggle-sidebar");
const sidebarElem = document.getElementById("sidebar");
const sidebarOverlay = document.getElementById("sidebar-overlay");
function fermerSidebar(){
  sidebarElem.classList.remove("ouvert");
  sidebarOverlay.classList.remove("ouvert");
}
// ── Sidebar : page « Table » (liste des tables) / page « Conf » (réglages, IA, etc.) ──
function sidebarActiverPage(page){
  // Trois pages : « table » (liste des tables), « actions » (actions de la table ouverte), « conf » (réglages, IA, etc.)
  const pages = {table: ["sidebar-page-table", "sidebar-tab-table"], actions: ["sidebar-page-actions", "sidebar-tab-actions"], conf: ["sidebar-bas", "sidebar-tab-conf"]};
  if(!pages[page]) page = "table";
  Object.entries(pages).forEach(([nom, [idPage, idTab]]) => {
    const p = document.getElementById(idPage), t = document.getElementById(idTab);
    if(p) p.style.display = nom === page ? "flex" : "none";
    if(t) t.classList.toggle("actif", nom === page);
  });
}
// Onglet « Actions » sans table ouverte : simple consigne.
function viderActionsSidebar(){
  const z = document.getElementById("sidebar-actions-table");
  if(z) z.innerHTML = tj("engine/static/engine/js/index_1.js:455:24");
}
// Sur mobile, un bouton d'action ferme la sidebar (sinon elle masque la fenêtre qu'il ouvre). Les interrupteurs la laissent ouverte.
document.getElementById("sidebar-actions-table")?.addEventListener("click", (e) => {
  if(window.innerWidth <= 768 && e.target.closest("button")) fermerSidebar();
});
if(btnToggleSidebar && sidebarElem){
  btnToggleSidebar.onclick = (e) => {
    e.stopPropagation();
    if(window.innerWidth <= 768){
      const estOuvert = sidebarElem.classList.contains("ouvert");
      if(estOuvert){ fermerSidebar(); }
      else { sidebarElem.classList.add("ouvert"); sidebarOverlay.classList.add("ouvert"); }
    } else {
      // PC : masquer/afficher directement
      sidebarElem.classList.toggle("masque");
    }
  };
  sidebarOverlay.onclick = fermerSidebar;
}
// Rafraîchir la liste des tables
// ⓘ à gauche de chaque entrée de la liste des tables : un nom trop long coupe le nombre de lignes / colonnes ; ce bouton les affiche dans un popup.
window.gyBoutonInfoTable = (htmlNom, htmlDetail) => {
  const b = document.createElement("button");
  b.type = "button"; b.className = "gy-info-table"; b.textContent = "ⓘ";
  b.title = tj("engine/static/engine/js/index_1.js:info_table"); b.setAttribute("aria-label", b.title);
  b.onclick = (ev) => {
    ev.stopPropagation();
    const ancien = document.getElementById("gy-info-table-popup");
    if (ancien) { ancien.remove(); if (ancien._bouton === b) return; }
    const pop = document.createElement("div");
    pop.id = "gy-info-table-popup"; pop._bouton = b;
    pop.innerHTML = `<div class="gy-info-nom">${htmlNom}</div><div class="gy-info-detail">${htmlDetail}</div>`;
    document.body.appendChild(pop);
    const r = b.getBoundingClientRect();
    pop.style.left = Math.max(8, Math.min(r.left, window.innerWidth - pop.offsetWidth - 8)) + "px";
    pop.style.top = Math.max(8, Math.min(r.bottom + 6, window.innerHeight - pop.offsetHeight - 8)) + "px";
    const fermer = (e) => { if (!pop.contains(e.target)) { pop.remove(); document.removeEventListener("click", fermer, true); } };
    setTimeout(() => document.addEventListener("click", fermer, true), 0);
  };
  return b;
};
// ▦ à côté du ⓘ : popup qui liste les colonnes de la table et leur type (la table est relue dans `tables` au clic : toujours à jour).
// Sert aussi aux groupes (dessinerGroupesConteneurs, index_7.js) : leurs parties partagent la même structure, on lit celle de l'ancre.
window.gyBoutonColonnesTable = (slug) => {
  const b = document.createElement("button");
  b.type = "button"; b.className = "gy-cols-table"; b.textContent = "▦";
  b.title = tj("engine/static/engine/js/index_1.js:colonnes_table_titre"); b.setAttribute("aria-label", b.title);
  b.onclick = (ev) => {
    ev.stopPropagation();
    const ancien = document.getElementById("gy-info-table-popup");   // même popup que le ⓘ : un seul ouvert à la fois
    if (ancien) { ancien.remove(); if (ancien._bouton === b) return; }
    const t = (typeof tables !== "undefined" ? tables : []).find(x => x.slug === slug);
    const champs = (t && t.champs) || [];
    const lignes = champs.length
      ? champs.map(c => `<tr><td class="gy-col-nom">${escapeHtml(c.nom)}</td><td class="gy-col-type">${escapeHtml(c.type_champ || c.type || "")}</td></tr>`).join("")
      : `<tr><td colspan="2">${escapeHtml(tj("engine/static/engine/js/index_1.js:colonnes_table_vide"))}</td></tr>`;
    const pop = document.createElement("div");
    pop.id = "gy-info-table-popup"; pop._bouton = b; pop.className = "gy-popup-colonnes";
    pop.innerHTML = `<div class="gy-info-nom">${escapeHtml((t && t.nom) || slug)}</div>
      <div class="gy-info-detail">${escapeHtml(tj("engine/static/engine/js/index_1.js:colonnes_table_entete", {v1: champs.length}))}</div>
      <table class="gy-cols-liste"><tbody>${lignes}</tbody></table>`;
    document.body.appendChild(pop);
    const r = b.getBoundingClientRect();
    pop.style.left = Math.max(8, Math.min(r.left, window.innerWidth - pop.offsetWidth - 8)) + "px";
    pop.style.top = Math.max(8, Math.min(r.bottom + 6, window.innerHeight - pop.offsetHeight - 8)) + "px";
    const fermer = (e) => { if (!pop.contains(e.target)) { pop.remove(); document.removeEventListener("click", fermer, true); } };
    setTimeout(() => document.addEventListener("click", fermer, true), 0);
  };
  return b;
};
// Un seul rafraîchissement à la fois : la barre latérale est vidée puis remplie avec des `await` entre les deux (tables, conteneurs, groupes).
// Deux appels simultanés (clic + rafraîchissement automatique, ouverture d'une partie…) ajoutaient chacun les groupes après que l'autre eut
// vidé la liste : tables une fois, groupes en double. Les appels sont donc mis en file ; chacun rend la main une fois la liste à jour.
let _chaineRafraichissementTables = Promise.resolve();
function rafraichirTables(){
  const suite = _chaineRafraichissementTables.catch(() => {}).then(_rafraichirTablesUnique);
  _chaineRafraichissementTables = suite;
  return suite;
}
async function _rafraichirTablesUnique(){
  const [data, rc] = await Promise.all([api("/api/tables/"), api("/api/conteneurs-tables/").catch(() => ({conteneurs: []}))]);
  tables = data.tables;
  // Table système ouverte par lien (?table=systeme_…) : /api/tables/ ne les liste pas sans ?systeme=1, on la garde pour cette page.
  if(window._tableSystemeDemandee && !tables.some(t => t.slug === window._tableSystemeDemandee.slug)) tables.push(window._tableSystemeDemandee);
  // Tables / groupes rangés dans un conteneur : visibles seulement DANS leur conteneur (sinon doublons dans la liste).
  const rT = new Set();
  for(const c of rc.conteneurs){ c.tables.forEach(x => rT.add(x.id)); }
  const conteneur = document.getElementById("liste-tables");
  conteneur.innerHTML = "";
  if(tables.length === 0){
    conteneur.innerHTML = tj("engine/static/engine/js/index_1.js:485:28");
  }
  for(const t of tables){
    // Une seule place par table : rangée dans un conteneur ou membre d'un groupe → jamais seule dans la liste
    // (elle apparaît dans son groupe, lui-même seul ou dans son conteneur — voir dessinerGroupesConteneurs).
    if(rT.has(t.id) || t.groupe_id) continue;
    const div = document.createElement("div");
    div.className = "item-table" + ((modeVue==="table"||modeVue==="dashboard"||modeVue==="documents") && tableActive===t.slug ? " actif" : "");
    const icone = t.est_table_membres ? '🪪 ' : (t.groupe_id ? '🔗 ' : (t.connexion_externe_origine_id ? '🔌 ' : (t.provient_fichier ? '📄 ' : '')));
    const partie = t.groupe_id ? ` · ${escapeHtml(t.groupe_nom)}${t.partie_total ? " " + t.partie_index + "/" + t.partie_total : ""}` : "";
    const detail = tj("engine/static/engine/js/index_1.js:527:67", {v1: t.nombre_lignes, v2: (t.champs||[]).length, v3: partie});
    div.innerHTML = `${icone}${escapeHtml(t.nom)} <span class="n">${detail}</span>`;
    if(window.GYApparence){ window.GYApparence.appliquerTable(div, t.slug); div.prepend(window.GYApparence.boutonTable(t.slug, div)); }   // Aa : fond + texte de l'entrée (apparence_tables.js)
    div.prepend(window.gyBoutonColonnesTable(t.slug));   // ▦ : colonnes + types (placé après le ⓘ, qui est prepend ensuite)
    const btnRen = document.createElement("button"); btnRen.type = "button"; btnRen.className = "gy-info-table"; btnRen.textContent = "✎"; btnRen.title = tj("decoupage.ui.renommer");   // ✎ : renommer la table (nom d'affichage seulement)
    btnRen.onclick = async (ev) => { ev.stopPropagation(); const nom = await GY.demander(tj("decoupage.ui.prompt_renommer"), t.nom); if(nom && nom.trim() && nom.trim() !== t.nom){ const r = await api("/api/tables/" + t.id + "/renommer/", {method: "POST", body: JSON.stringify({nom: nom.trim()})}); notifierSucces(tj("decoupage.ui.renomme_ok", {v1: r.table.nom})); rafraichirTables(); } };
    div.prepend(btnRen);
    div.prepend(window.gyBoutonInfoTable(`${icone}${escapeHtml(t.nom)}`, detail));   // ⓘ : nom complet + lignes / colonnes, même quand le nom long les fait disparaître
    div.onclick = () => {
      modeVue="table"; tableActive=t.slug; afficherTable(t.slug); rafraichirTables();
      if(window.innerWidth <= 768) fermerSidebar();
    };
    conteneur.appendChild(div);
  }
  if(window.dessinerGroupesConteneurs) await window.dessinerGroupesConteneurs(conteneur, rc.conteneurs);  // groupes (= une table) et conteneurs (index_7.js)
}
// Import de fichier (CSV/Excel) -> table statique, aucune saisie manuelle.
// Flow en deux temps :
//   1) utilisateur choisit un fichier -> on ouvre la modal d'import
//   2) si Excel : on appelle /api/tables/importer-fichier/feuilles/ pour
//      récupérer la liste des onglets et les proposer dans un <select>
//   3) sur validation, on POST le fichier + feuille + séparateur + encodage
//      vers /api/tables/importer-fichier/ qui crée la table.
const btnImporterFichier = document.getElementById("btn-importer-fichier");
const inputFichierImport = document.getElementById("input-fichier-import");
// Références vers les éléments de la modal d'import
const modalImportFond = document.getElementById("modal-import-fond");
const importNomFichier = document.getElementById("import-nom-fichier");
const importChampFeuille = document.getElementById("import-champ-feuille");
const importChampSeparateur = document.getElementById("import-champ-separateur");
const importChampEncodage = document.getElementById("import-champ-encodage");
const importSelectFeuille = document.getElementById("import-feuille");
const importSelectSeparateur = document.getElementById("import-separateur");
const importSelectEncodage = document.getElementById("import-encodage");
const importSelectStockage = document.getElementById("import-stockage");
const importErreur = document.getElementById("import-erreur");
const btnAnnulerImport = document.getElementById("btn-annuler-import");
const btnConfirmerImport = document.getElementById("btn-confirmer-import");
// L'état courant de la modale : fichier en attente + nature (excel/csv)
let _fichierEnAttente = null;
let _exportFideleDetecte = null;   // résumé serveur d'un export de la plateforme (table native / indépendante), sinon null
function _extFichier(nom){
  return (nom.split(".").pop() || "").toLowerCase();
}
function fermerModalImport(){
  modalImportFond.style.display = "none";
  _fichierEnAttente = null;
  _exportFideleDetecte = null;
  inputFichierImport.value = "";
  importErreur.style.display = "none";
  importErreur.textContent = "";
}
function afficherErreurImport(msg){
  importErreur.textContent = msg;
  importErreur.style.display = "block";
}
/**
 * Bloc « export de la plateforme » : dit si le fichier restaure une table NATIVE (elle existe encore ici : restauration sur place ou copie)
 * ou INDÉPENDANTE (à recréer), ou s'il s'agit d'un JSON ordinaire. Renvoie false si le fichier est refusé (export modifié à la main).
 */
function _afficherBlocFidele(res){
  const bloc = document.getElementById("import-bloc-fidele");
  const titre = document.getElementById("import-fidele-titre");
  const detail = document.getElementById("import-fidele-detail");
  const choix = document.getElementById("import-fidele-choix");
  _exportFideleDetecte = null;
  if(!bloc) return true;
  bloc.style.display = "none"; choix.style.display = "none";
  const f = res && res.export_fidele;
  if(f && f.erreur){
    afficherErreurImport(f.erreur);
    return false;
  }
  if(f){
    _exportFideleDetecte = f;
    bloc.style.display = "";
    const n = `${tj("engine/static/engine/js/index_1.js:596:16", {v1: f.nb_lignes, v2: f.nb_champs})}`;
    if(f.existante){
      titre.textContent = tj("engine/static/engine/js/index_1.js:566:28", {v1: f.nom});
      detail.textContent = tj("engine/static/engine/js/index_1.js:567:29", {v1: n});
      document.getElementById("import-fidele-lib-remplacer").textContent =
        tj("engine/static/engine/js/index_1.js:569:10", {v1: f.existante.nom, v2: f.existante.nb_lignes});
      choix.style.display = "";
      const radio = bloc.querySelector('input[name="import-mode-restauration"][value="remplacer"]');
      if(radio) radio.checked = true;
    } else {
      titre.textContent = tj("engine/static/engine/js/index_1.js:574:28", {v1: f.nom});
      detail.textContent = tj("engine/static/engine/js/index_1.js:575:29", {v1: n});
    }
  } else if(res && res.json_independant){
    bloc.style.display = "";
    titre.textContent = tj("engine/static/engine/js/index_1.js:579:26");
    detail.textContent = tj("engine/static/engine/js/index_1.js:580:27")
      + tj("engine/static/engine/js/index_1.js:581:10");
  }
  return true;
}
/**
 * Ouvre la modal d'import pour le fichier sélectionné. Si c'est un Excel,
 * charge en arrière-plan la liste des feuilles et peuple le <select>.
 */
async function ouvrirModalImport(fichier){
  _fichierEnAttente = fichier;
  importNomFichier.textContent = `${fichier.name}  (${(fichier.size/1024).toFixed(1)} ${tj("engine/static/engine/js/index_1.js:lbl_unite_ko")})`;
  importErreur.style.display = "none";
  importErreur.textContent = "";

  const ext = _extFichier(fichier.name);
  const estExcel = ext === "xlsx" || ext === "xls";
  const estCsv = ext === "csv";
  const estJson = ext === "json";
  const estParquet = ext === "parquet";
  let _refuse = false;   // export de la plateforme modifié à la main : import bloqué
  _exportFideleDetecte = null;
  const _blocF = document.getElementById("import-bloc-fidele");
  if(_blocF) _blocF.style.display = "none";

  // Réinitialiser les dropdowns
  importSelectFeuille.innerHTML = tj("engine/static/engine/js/index_1.js:557:36");
  importSelectSeparateur.value = "auto";
  importSelectEncodage.value = "auto";

  // Afficher les bons champs selon le format
  importChampFeuille.style.display = estExcel ? "" : "none";
  importChampSeparateur.style.display = estCsv ? "" : "none";
  importChampEncodage.style.display = estCsv ? "" : "none";

  // Si Excel : récupérer les feuilles en arrière-plan
  if(estExcel){
    importChampFeuille.querySelector("label").textContent = tj("engine/static/engine/js/index_1.js:568:62");
    try {
      const fd = new FormData();
      fd.append("fichier", fichier);
      const res = await api("/api/tables/importer-fichier/feuilles/", {method:"POST", body: fd});
      importSelectFeuille.innerHTML = tj("engine/static/engine/js/index_1.js:573:40");
      for(const nom of (res.feuilles || [])){
        importSelectFeuille.innerHTML += `<option value="${escapeHtml(nom)}">${escapeHtml(nom)}</option>`;
      }
      importChampFeuille.querySelector("label").textContent = tj("engine/static/engine/js/index_1.js:lbl_feuille_onglet_excel");
      if(!_afficherBlocFidele(res)){ _refuse = true; }
      if(_exportFideleDetecte) importChampFeuille.style.display = "none";   // export de la plateforme : pas de choix d'onglet
      else if(!res.feuilles || res.feuilles.length === 0){
        afficherErreurImport(tj("engine/static/engine/js/index_1.js:579:31"));
      }
    } catch(e){
      importChampFeuille.querySelector("label").textContent = tj("engine/static/engine/js/index_1.js:lbl_feuille_onglet_excel");
      afficherErreurImport(tj("engine/static/engine/js/index_1.js:583:29"));
    }
  }

  // JSON ou Parquet : export de la plateforme (table native / indépendante, manifeste lu côté serveur) ou fichier ordinaire
  if(estJson || estParquet){
    try {
      const fd = new FormData();
      fd.append("fichier", fichier);
      const res = await api("/api/tables/importer-fichier/feuilles/", {method:"POST", body: fd});
      if(!_afficherBlocFidele(res)){ _refuse = true; }
    } catch(e){
      afficherErreurImport(tj("engine/static/engine/js/index_1.js:645:29"));
    }
  }

  // Si format non reconnu : on laisse l'utilisateur tenter mais on prévient
  if(!estExcel && !estCsv && !estJson && !estParquet){
    afficherErreurImport(tj("engine/static/engine/js/index_1.js:589:27", {v1: ext}));
    btnConfirmerImport.disabled = true;
  } else {
    btnConfirmerImport.disabled = _refuse;
  }

  modalImportFond.style.display = "flex";
}
// ── Limite de lignes globale (panneau lateral) ──────────────────────────────
// Un seul reglage pour toute l'entreprise, applique immediatement a TOUTE
// table (native ou importee) — remplace l'ancien controle par table (barre
// d'outils) et le champ de la modal d'import, tous deux retires.
async function chargerLimiteLignesGlobale(){
  const input = document.getElementById("input-limite-lignes-globale");
  if(!input) return;
  try {
    const res = await api("/api/limite-lignes/");
    input.value = res.limite_lignes;
  } catch(e) { /* déjà notifié par api() */ }
}
const btnLimiteLignesGlobale = document.getElementById("btn-limite-lignes-globale");
if(btnLimiteLignesGlobale){
  btnLimiteLignesGlobale.onclick = async (ev) => {
    const input = document.getElementById("input-limite-lignes-globale");
    const brut = input.value;
    if(brut === ""){ notifierErreur(tj("engine/static/engine/js/index_1.js:614:38")); return; }
    ev.target.textContent = "…";
    try {
      const res = await api("/api/limite-lignes/sauvegarder/", {
        method: "POST", body: JSON.stringify({limite_lignes: parseInt(brut, 10)}),
      });
      input.value = res.limite_lignes;
      notifierSucces(tj("engine/static/engine/js/index_1.js:621:23"));
    } catch(e) { /* déjà notifié par api() */ }
    finally { ev.target.textContent = "OK"; }
  };
}
// ── Limite de colonnes globale (panneau lateral) ─────────────────────────────
// Symétrique à limite_lignes : un seul réglage pour toute l'entreprise,
// appliqué à TOUTE table (native ou importée) — visible uniquement pour l'admin.
async function chargerLimiteColonnesGlobale(){
  const input = document.getElementById("input-limite-colonnes-globale");
  if(!input) return;
  try {
    const res = await api("/api/limite-colonnes/");
    input.value = res.limite_colonnes;
  } catch(e) { /* déjà notifié par api() */ }
}
const btnLimiteColonnesGlobale = document.getElementById("btn-limite-colonnes-globale");
if(btnLimiteColonnesGlobale){
  btnLimiteColonnesGlobale.onclick = async (ev) => {
    const input = document.getElementById("input-limite-colonnes-globale");
    const brut = input.value;
    if(brut === ""){ notifierErreur(tj("engine/static/engine/js/index_1.js:642:38")); return; }
    ev.target.textContent = "…";
    try {
      const res = await api("/api/limite-colonnes/sauvegarder/", {
        method: "POST", body: JSON.stringify({limite_colonnes: parseInt(brut, 10)}),
      });
      input.value = res.limite_colonnes;
      notifierSucces(tj("engine/static/engine/js/index_1.js:649:23"));
    } catch(e) { /* déjà notifié par api() */ }
    finally { ev.target.textContent = "OK"; }
  };
}
if(btnImporterFichier && inputFichierImport){
  btnImporterFichier.onclick = () => inputFichierImport.click();
  inputFichierImport.addEventListener("change", () => {
    const fichier = inputFichierImport.files[0];
    if(!fichier) return;
    ouvrirModalImport(fichier);
  });
}

btnAnnulerImport.onclick = fermerModalImport;
// Fermer aussi en cliquant sur le fond
modalImportFond.onclick = (e) => {
  if(e.target === modalImportFond) fermerModalImport();
};

btnConfirmerImport.onclick = async () => {
  if(!_fichierEnAttente) return;
  const fichier = _fichierEnAttente;
  const _radioMode = document.querySelector('input[name="import-mode-restauration"]:checked');
  const modeRestauration = (_exportFideleDetecte && _exportFideleDetecte.existante && _radioMode) ? _radioMode.value : "copie";
  if(modeRestauration === "remplacer" &&
     !await GY.confirmer(tj("engine/static/engine/js/index_1.js:737:16", {v1: _exportFideleDetecte.existante.nb_lignes, v2: _exportFideleDetecte.existante.nom, v3: _exportFideleDetecte.nb_lignes}))) return;
  const feuille = importSelectFeuille.value;
  const separateur = importSelectSeparateur.value;
  const encodage = importSelectEncodage.value;
  const typeStockage = importSelectStockage ? importSelectStockage.value : "sql";

  btnConfirmerImport.disabled = true;
  btnConfirmerImport.textContent = tj("engine/static/engine/js/index_1.js:678:37");
  btnImporterFichier.textContent = tj("engine/static/engine/js/index_1.js:679:37");
  btnImporterFichier.disabled = true;
  importErreur.style.display = "none";

  const donneesForm = new FormData();
  donneesForm.append("fichier", fichier);
  if(feuille) donneesForm.append("feuille", feuille);
  if(separateur) donneesForm.append("separateur", separateur);
  if(encodage) donneesForm.append("encodage", encodage);
  donneesForm.append("type_stockage", typeStockage);
  if(_exportFideleDetecte) donneesForm.append("mode_restauration", modeRestauration);

  try{
    // L'import est maintenant asynchrone : le serveur répond immédiatement
    // avec {tache_id} et le worker Celery fait le vrai travail.
    const res = await api("/api/tables/importer-fichier/", {method:"POST", body: donneesForm});
    fermerModalImport();
    // Polling jusqu'à la fin de la tâche
    await _pollerImport(res.tache_id, res.nom_fichier);
  } catch(e) {
    if(e && e.message) afficherErreurImport(e.message);
    btnConfirmerImport.disabled = false;
    btnConfirmerImport.textContent = tj("engine/static/engine/js/index_1.js:700:39");
    btnImporterFichier.textContent = tj("engine/static/engine/js/index_1.js:701:39");
    btnImporterFichier.disabled = false;
  }
};

async function _pollerImport(tacheId, nomFichier){
  // Affiche une notification persistante pendant l'import
  const toastId = "import-" + tacheId;
  _afficherToastImport(toastId, nomFichier, 0);
  let tentatives = 0;
  const MAX = 360; // 6 min max
  while(tentatives < MAX){
    await new Promise(r => setTimeout(r, 2000));
    tentatives++;
    let d;
    try{ d = await api("/api/import/" + tacheId + "/statut/"); } catch(e){ continue; }
    _afficherToastImport(toastId, nomFichier, d.progression, d.diagnostic);
    if(d.fini){
      _supprimerToastImport(toastId);
      if(d.statut === "termine" && d.table){
        notifierSucces(_messageImportTermine(d));
        if(d.erreur) notifierErreur(d.erreur);   // restauration fidèle : ce qui n'a pas pu être rétabli à l'identique
        await rafraichirTables();
        modeVue = "dashboard"; tableActive = d.table.slug;
        afficherTableauBord(d.table.slug);
        if(window.innerWidth <= 768) fermerSidebar();
      } else {
        notifierErreur(d.erreur || tj("engine/static/engine/js/index_1.js:728:37"));
      }
      btnConfirmerImport.disabled = false;
      btnConfirmerImport.textContent = tj("engine/static/engine/js/index_1.js:731:41");
      btnImporterFichier.textContent = tj("engine/static/engine/js/index_1.js:732:41");
      btnImporterFichier.disabled = false;
      return;
    }
  }
  _supprimerToastImport(toastId);
  btnConfirmerImport.disabled = false; btnConfirmerImport.textContent = tj("engine/static/engine/js/index_1.js:738:74");
  btnImporterFichier.textContent = tj("engine/static/engine/js/index_1.js:739:37"); btnImporterFichier.disabled = false;
  notifierErreur(tj("engine/static/engine/js/index_1.js:740:19"));
}
// Titre des vues d'analyse : nom + groupe (les analyses portent sur TOUTES les tables du groupe).
// textContent : jamais d'escapeHtml ici (sinon « &amp; » affiché).
function _titreAnalyse(table){
  return table.nom + (table.groupe_id ? tj("engine/static/engine/js/index_1.js:745:42") + table.groupe_nom + " »" : "");
}
// Message de fin d'import — partagé par le suivi immédiat et la reprise à la reconnexion (index_4.js).
function _messageImportTermine(d){
  return d.groupe
    ? tj("engine/static/engine/js/index_1.js:750:8", {v1: d.groupe.nom, v2: d.groupe.nb_tables, v3: d.groupe.nb_lignes_total})
    : tj("engine/static/engine/js/index_1.js:751:8", {v1: d.table.nom, v2: d.table.nombre_lignes});
}

function _afficherToastImport(id, nom, pct, diagnostic){
  let el = document.getElementById("toast-import-" + id);
  if(!el){
    el = document.createElement("div");
    el.id = "toast-import-" + id;
    el.style.cssText = "position:fixed;bottom:70px;right:16px;background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:10px 14px;min-width:220px;z-index:8000;font-size:12px";
    document.body.appendChild(el);
  }
  el.innerHTML = `<div style="color:var(--text);margin-bottom:6px">${tj("engine/static/engine/js/index_1.js:862:68", {v1: nom})}</div>
    <div style="background:var(--border);border-radius:3px;height:4px">
      <div style="background:var(--accent);width:${pct}%;height:4px;border-radius:3px;transition:width .4s"></div>
    </div>
    <div style="color:var(--muted);margin-top:4px">${pct}%</div>
    ${diagnostic ? `<div style="color:var(--texte-erreur,#ff8a80);margin-top:6px;max-width:320px;overflow-wrap:break-word">${escapeHtml(diagnostic)}</div>` : ""}`;
}
function _supprimerToastImport(id){
  const el = document.getElementById("toast-import-" + id);
  if(el) el.remove();
}
// Vue vide
function afficherVide(){
  modeVue = "vide"; tableActive = null;
  { const _et = document.getElementById("entete-titre"); if(_et) _et.textContent = window.gyNomApp || tj("engine/static/engine/js/marque.js:nom_application"); }  // nom choisi par l'utilisateur (marque.js), sinon nom d'origine
  viderActionsSidebar();
  document.getElementById("zone-contenu").innerHTML = `
    <div style="color:var(--muted);max-width:520px;margin:60px auto;text-align:center">
      ${tables.length === 0
        ? tj("engine/static/engine/js/index_1.js:783:12")
        : tj("engine/static/engine/js/index_1.js:784:12")}
    </div>`;
}
// Barre d'actions de la table (Prompt, Structure, Lien dédié, Supprimer, Échantillon)
// Partagée entre la vue Données et la vue Dashboard pour que ces boutons restent
// toujours visibles, quel que soit l'onglet actif.
function rendreActionsTable(table, slug){
  // modeVue est déjà positionné par l'appelant (afficherTable ou afficherTableauBord)
  // Le bouton « Modules » vit uniquement dans la sidebar (onglet « Actions ») : il n'y a plus de rangée d'actions dans l'en-tête.
  // Le reste des actions de la table vit dans l'onglet « Actions » de la sidebar (voir sidebarActiverPage).
  document.getElementById("sidebar-actions-table").innerHTML = `
    <div class="barre-outils-actions">
      <button id="btn-modules-menu" style="font-size:12px;padding:4px 8px" title="${tj("engine/static/engine/js/index_1.js:modules_titre")}">${tj("engine/static/engine/js/index_1.js:modules_bouton")}</button>
      <div style="display:flex;align-items:center;gap:4px">
        <span style="color:var(--muted);font-size:11px">${tj("engine/static/engine/js/index_1.js:815:58")}</span>
        <input id="input-echantillon" type="number" min="1" max="50" value="${table.taille_echantillon ?? 5}" style="width:48px;padding:4px 6px">
        <button id="btn-echantillon" style="padding:4px 8px;font-size:12px">OK</button>
      </div>
      <button id="btn-prompt-rapport" style="font-size:12px;padding:4px 8px">${tj("engine/static/engine/js/index_1.js:900:78")}</button>
      <button id="btn-structure-table" style="font-size:12px;padding:4px 8px">${tj("engine/static/engine/js/index_1.js:901:79")}</button>
      ${(table.nombre_lignes ?? 0) >= 2 && !table.est_table_membres ? `<button id="btn-decouper-table" style="font-size:12px;padding:4px 8px" title="${tj("decoupage.ui.titre", {v1: table.nom})}">${tj("decoupage.ui.decouper")}</button>` : ""}
      <button id="btn-formulaire-table" style="font-size:12px;padding:4px 8px" title="${tj("engine/static/engine/js/index_1.js:formulaire_bouton_titre")}">${table.formulaire_conditionnel ? "🔀 " + tj("engine/static/engine/js/index_1.js:formulaire_bouton_cond") : "📝 " + tj("engine/static/engine/js/index_1.js:formulaire_bouton")}</button>
      ${table.est_table_membres ? `<button id="btn-carte-table" style="font-size:12px;padding:4px 8px">${tj("engine/static/engine/js/index_1.js:821:105")}</button>` : ""}
      ${table.est_table_membres && !table.est_table_produits ? `<span style="font-size:11px;color:var(--accent,#6bcfff);padding:4px 8px;border:1px solid var(--border,#2a2a2a);border-radius:6px" title="${tj("engine/static/engine/js/index_1.js:822:146")}">👤 ${table.nb_clients_codes} ${tj("engine/static/engine/js/index_1.js:822:279")}</span>` : ""}
      ${table.est_table_membres ? `<label class="toggle-mini" id="toggle-table-produits" title="${tj("engine/static/engine/js/index_1.js:g:table_produits_titre")}">
        <div class="pill"><div class="knob"></div></div>
        <span>${tj("engine/static/engine/js/index_1.js:g:table_produits_label")}</span>
      </label>` : ""}
      <label class="toggle-mini" id="toggle-expose-client" title="${tj("engine/static/engine/js/index_1.js:823:68")}">
        <div class="pill"><div class="knob"></div></div>
        <span>${tj("engine/static/engine/js/index_1.js:825:16")}</span>
      </label>
      <label class="toggle-mini" id="toggle-masquer-formulaire-client" title="${tj("engine/static/engine/js/index_1.js:827:80")}">
        <div class="pill"><div class="knob"></div></div>
        <span>${tj("engine/static/engine/js/index_1.js:829:16")}</span>
      </label>
      <span id="badge-table-denaturee" style="font-size:11px;color:var(--texte-orange,#e0a030);padding:4px 8px;border:1px solid var(--border,#2a2a2a);border-radius:6px;display:${table.denaturee ? "inline" : "none"}" title="${tj("engine/static/engine/js/index_1.js:a:900:189")}">${tj("engine/static/engine/js/index_1.js:831:470")}</span>
      <button id="btn-exporter-table" style="font-size:12px;padding:4px 8px" title="${tj("engine/static/engine/js/index_1.js:832:86")}">${tj("engine/static/engine/js/index_1.js:917:137")}</button>
      <button id="btn-exporter-table-pdf" style="font-size:12px;padding:4px 8px" title="${tj("engine/static/engine/js/index_1.js:833:90")}">${tj("engine/static/engine/js/index_1.js:918:141")}</button>
      <button id="btn-resume-general-pdf" style="font-size:12px;padding:4px 8px" title="${tj("engine/static/engine/js/index_1.js:834:90")}">${tj("engine/static/engine/js/index_1.js:834:166")}</button>
      <button id="btn-rapport-general-pdf" style="font-size:12px;padding:4px 8px" title="${tj("engine/static/engine/js/index_1.js:rg_aide")}">${tj("engine/static/engine/js/index_1.js:rg_bouton")}</button>
      <button id="btn-fiche-objet-table" style="font-size:12px;padding:4px 8px" title="${tj("engine/static/engine/js/index_1.js:835:89")}">${tj("engine/static/engine/js/index_1.js:921:140")}</button>
      <button id="btn-supprimer-table" class="danger" style="font-size:12px;padding:4px 8px">${tj("engine/static/engine/js/index_1.js:836:95")}</button>
    </div>`;

  document.querySelector("#btn-fiche-objet-table").onclick = () => { ouvrirFicheObjet(tj("engine/static/engine/js/index_1.js:839:88", {v1: table.id})); };

  // Bouton « Modules » de la sidebar (onglet « Actions »).
  document.querySelector("#btn-modules-menu").onclick = () => { window.location.href = `/tables/${table.id}/modules/`; };
  document.querySelector("#btn-prompt-rapport").onclick = () => ouvrirModalPrompt(table);
  const btnCarte = document.querySelector("#btn-carte-table");
  if (btnCarte) btnCarte.onclick = () => ouvrirModalCarte(table);
  document.querySelector("#btn-echantillon").onclick = async (ev) => {
    const valeur = parseInt(document.getElementById("input-echantillon").value, 10);
    ev.target.textContent = "…";
    try {
      await api(`/api/tables/${table.id}/modifier/`, {method:"POST", body: JSON.stringify({taille_echantillon: valeur})});
      notifierSucces(tj("engine/static/engine/js/index_1.js:856:23"));
      rafraichirTables();
    } catch(e) { /* déjà notifié par api() */ }
    finally { ev.target.textContent = "OK"; }
  };
  // Toggle expose_client
  const toggleProd = document.querySelector("#toggle-table-produits");
  if(toggleProd){
    if(table.est_table_produits) toggleProd.classList.add("on");
    toggleProd.onclick = async () => {
      const actuel = toggleProd.classList.contains("on");
      try{
        const res = await api(`/api/tables/${table.id}/table-produits/`, {method:"POST", body:JSON.stringify({produits:!actuel})});
        notifierSucces(res.est_table_produits ? tj("engine/static/engine/js/index_1.js:g:table_produits_on") : tj("engine/static/engine/js/index_1.js:g:table_produits_off"));
        await rafraichirTables();
        afficherTable(table.slug);   // recharge l'en-tête et la grille : la colonne du code d'accès apparaît / disparaît
      }catch(e){}
    };
  }
  const toggleExp = document.querySelector("#toggle-expose-client");
  if(table.expose_client) toggleExp.classList.add("on");
  toggleExp.onclick = async () => {
    const actuel = toggleExp.classList.contains("on");
    try{
      const res = await api(`/api/tables/${table.id}/expose-client/`, {method:"POST", body:JSON.stringify({expose_client:!actuel})});
      res.expose_client ? toggleExp.classList.add("on") : toggleExp.classList.remove("on");
      table.expose_client = res.expose_client;
      notifierSucces(res.expose_client ? tj("engine/static/engine/js/index_1.js:883:43") : tj("engine/static/engine/js/index_1.js:883:79"));
      rafraichirTables();
    }catch(e){}
  };
  // Toggle masquer_formulaire_client ("Recherche") : totalement independant
  // du toggle "Client" (formulaire). Les deux se combinent librement :
  // Client seul -> formulaire seul, Recherche seule -> recherche seule,
  // les deux actifs -> les deux blocs, aucun actif -> table invisible
  // cote client (voir TableDynamique.accessible_client cote backend).
  const toggleRechercheSeule = document.querySelector("#toggle-masquer-formulaire-client");
  if(table.masquer_formulaire_client) toggleRechercheSeule.classList.add("on");
  toggleRechercheSeule.onclick = async () => {
    const actuel = toggleRechercheSeule.classList.contains("on");
    try{
      const res = await api(`/api/tables/${table.id}/masquer-formulaire-client/`, {method:"POST", body:JSON.stringify({masquer_formulaire_client:!actuel})});
      res.masquer_formulaire_client ? toggleRechercheSeule.classList.add("on") : toggleRechercheSeule.classList.remove("on");
      table.masquer_formulaire_client = res.masquer_formulaire_client;
      notifierSucces(res.masquer_formulaire_client
        ? tj("engine/static/engine/js/index_1.js:901:12")
        : tj("engine/static/engine/js/index_1.js:902:12"));
      rafraichirTables();
    }catch(e){}
  };
  // Statut "dénaturée" : plus aucun toggle manuel (voir TableDynamique.
  // recalculer_denaturee côté backend) — calculé automatiquement à chaque
  // création/suppression de fusion, affiché en lecture seule ci-dessus.
  // Le changement de type d'une seule cellule et la fusion de plusieurs
  // cellules se déclenchent désormais directement depuis la grille (icône
  // sur la cellule sélectionnée, ou barre de fusion automatique dès 2
  // cellules sélectionnées) — voir _rafraichirSurbrillance / _ouvrirBarreFusion.
  document.querySelector("#btn-supprimer-table").onclick = async () => {
    // Un groupe s'ouvre comme UNE table : « supprimer » emporte alors tout le groupe (toutes ses parties), pas seulement la première.
    const groupe = !!table.groupe_id;
    const parties = groupe ? tables.filter(t => t.groupe_id === table.groupe_id) : [];
    const nom = groupe ? (table.groupe_nom || table.nom) : table.nom;
    const question = groupe
      ? tj("suppression.groupe.confirmer", {v1: nom, v2: parties.length, v3: parties.reduce((n, t) => n + (t.nombre_lignes || 0), 0)})
      : tj("engine/static/engine/js/index_1.js:914:18", {v1: table.nom});
    if(!await GY.confirmer(question)) return;
    if(!await window.gySupprimerEnMasse(`/api/tables/${table.id}/supprimer/`, {method: "POST", body: JSON.stringify({groupe})}, nom)) return;
    delete scriptsEnAttente[slug];
    await rafraichirTables();
    afficherVide();
  };

  document.querySelector("#btn-structure-table")?.addEventListener("click", () => ouvrirModalStructure(table));
  document.querySelector("#btn-decouper-table")?.addEventListener("click", () => window.gyDecouperTable?.({id: table.id, nom: table.nom, nb: table.nombre_lignes}, async () => {   // la table d'origine devient la partie 1 (même id/slug)
    await rafraichirTables();
    if(modeVue === "dashboard") afficherTableauBord(slug); else afficherTable(slug);
  }));
  document.querySelector("#btn-formulaire-table")?.addEventListener("click", () => window.gyFormulaireConditionnel.ouvrir(table));

  // Export complet par le serveur (comme les boutons CSV/XLSX de la grille) : l'ancien export lisait /lignes/, limité à
  // 200 lignes par page, et tronquait donc toute table plus grande. NoSQL : le serveur renvoie du JSON.
  // « Exporter la table » = copie EXACTE (schéma + valeurs brutes) : réimportée, elle restaure la table à l'identique. CSV/Excel de la grille = vues pour lire ailleurs.
  document.querySelector("#btn-exporter-table").onclick = (ev) => gyTelecharger(
    `/api/tables/${table.id}/exporter-tableur/?format=gytable`,
    `${table.slug}.gytable.json`,
    {bouton: ev.currentTarget, succes: tj("engine/static/engine/js/index_1.js:932:41")});

  // Deux PDF, un seul téléchargement : liste de la table, et « Résumé général » (paysage : liste, dashboards, ontologie, stats).
  const _pdfTable = (idBouton, url, nomFichier, messageSucces, methode = "GET") => {
    const btn = document.querySelector(idBouton);
    btn.onclick = () => gyTelecharger(url, nomFichier, {methode, bouton: btn, succes: messageSucces});
  };
  _pdfTable("#btn-exporter-table-pdf", `/api/tables/${table.id}/exporter-pdf/`, `${table.slug}.pdf`, tj("engine/static/engine/js/index_1.js:939:103"));
  _pdfTable("#btn-resume-general-pdf", `/api/tables/${table.id}/resume-general-pdf/`, `${table.slug}_resume_general.pdf`, tj("engine/static/engine/js/index_1.js:940:124"), "POST");  // la route du résumé n'accepte que POST
  // « Rapport général » : l'utilisateur choisit d'abord la portée (cette table, toute son activité, et pour le super-administrateur toute l'entreprise).
  const btnRapportGeneral = document.querySelector("#btn-rapport-general-pdf");
  btnRapportGeneral.onclick = async () => {
    const choix = await choisirPorteeRapportGeneral();
    if(!choix) return;
    const portee = choix.portee;
    const nomFichier = portee === "table" ? `${table.slug}_rapport_general.pdf` : portee === "mes_tables" ? "rapport_general_mon_activite.pdf" : "rapport_general_entreprise.pdf";
    // Sections cochées : rien de coché = pas de paramètre = le serveur prend tout (comportement historique), aux trois portées.
    const parametreSections = choix.sections.length ? `&sections=${encodeURIComponent(choix.sections.join(","))}` : "";
    gyTelecharger(`/api/tables/${table.id}/rapport-general-pdf/?portee=${encodeURIComponent(portee)}${parametreSections}`, nomFichier,
      {methode: "POST", bouton: btnRapportGeneral, succes: tj("engine/static/engine/js/index_1.js:rg_succes")});
  };
}
// Fenêtre de choix du Rapport général : la portée (radios), puis les analyses à inclure (cases à cocher). Les portées et les sections
// viennent du serveur (/api/rapport-general/portees/) : « toute l'entreprise » n'y figure que pour le super-administrateur.
// Rien de coché = tout est inclus. Renvoie {portee, sections:[ids]} (sections vide = tout), ou null si l'utilisateur annule.
async function choisirPorteeRapportGeneral(){
  let portees = [{id: "table", libelle: tj("engine/static/engine/js/index_1.js:rg_dlg_portee_lib"), aide: tj("engine/static/engine/js/index_1.js:rg_dlg_portee_aide")}];
  let sections = [];
  try{
    const r = await fetch("/api/rapport-general/portees/", {credentials: "same-origin"});
    if(r.ok){
      const j = await r.json();
      if(Array.isArray(j.portees) && j.portees.length) portees = j.portees;
      if(Array.isArray(j.sections)) sections = j.sections;
    }
  }catch(e){ /* repli : la table courante seule, toutes les analyses */ }
  return new Promise(resolve => {
    let majGenerer = () => {};
    const voile = document.createElement("div");
    voile.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:10000;display:flex;align-items:center;justify-content:center;padding:16px";
    const carte = document.createElement("div");
    carte.setAttribute("role", "dialog");
    carte.style.cssText = "background:var(--bg-card,#1b1b1b);color:var(--text,#eee);border:1px solid var(--border,#2a2a2a);border-radius:10px;padding:16px;max-width:420px;width:100%;max-height:90vh;overflow:auto;font-size:14px";
    const titre = document.createElement("div");
    titre.style.cssText = "font-weight:600;margin-bottom:4px";
    titre.textContent = tj("engine/static/engine/js/index_1.js:rg_bouton");
    const intro = document.createElement("div");
    intro.style.cssText = "font-size:12px;opacity:.8;margin-bottom:10px";
    intro.textContent = tj("engine/static/engine/js/index_1.js:rg_dlg_intro");
    carte.append(titre, intro);
    portees.forEach((p, i) => {
      const ligne = document.createElement("label");
      ligne.style.cssText = "display:flex;gap:8px;align-items:flex-start;padding:8px;border:1px solid var(--border,#2a2a2a);border-radius:8px;margin-bottom:6px;cursor:pointer";
      const radio = document.createElement("input");
      radio.type = "radio"; radio.name = "portee-rapport-general"; radio.value = p.id; radio.checked = i === 0;
      const texte = document.createElement("span");
      const lib = document.createElement("strong"); lib.textContent = p.libelle;
      const aide = document.createElement("div"); aide.style.cssText = "font-size:12px;opacity:.75"; aide.textContent = p.aide || "";
      texte.append(lib, aide);
      ligne.append(radio, texte);
      carte.append(ligne);
    });
    if(sections.length){
      const entete = document.createElement("div");
      entete.style.cssText = "font-weight:600;margin:12px 0 2px";
      entete.textContent = tj("engine/static/engine/js/index_1.js:rg_dlg_contenu");
      const aideContenu = document.createElement("div");
      aideContenu.style.cssText = "font-size:12px;opacity:.75;margin-bottom:6px";
      aideContenu.textContent = tj("engine/static/engine/js/index_1.js:rg_dlg_contenu_aide");
      carte.append(entete, aideContenu);
      sections.forEach(sec => {
        const ligne = document.createElement("label");
        ligne.style.cssText = "display:flex;gap:8px;align-items:center;padding:4px 2px;cursor:pointer";
        const coche = document.createElement("input");
        coche.type = "checkbox"; coche.name = "section-rapport-general"; coche.value = sec.id;
        coche.checked = true;   // tout est coché au départ : on décoche ce qu'on ne veut PAS dans le rapport
        coche.addEventListener("change", () => majGenerer());
        const lib = document.createElement("span"); lib.textContent = sec.libelle;
        ligne.append(coche, lib);
        carte.append(ligne);
      });
    }
    const actions = document.createElement("div");
    actions.style.cssText = "display:flex;gap:8px;justify-content:flex-end;margin-top:10px";
    const annuler = document.createElement("button"); annuler.type = "button"; annuler.textContent = tj("engine/static/engine/js/index_1.js:rg_dlg_annuler");
    const generer = document.createElement("button"); generer.type = "button"; generer.textContent = tj("engine/static/engine/js/index_1.js:rg_dlg_generer");
    const fermer = (valeur) => { voile.remove(); resolve(valeur); };
    const coches = () => Array.from(carte.querySelectorAll('input[name="section-rapport-general"]'));
    majGenerer = () => { generer.disabled = coches().length > 0 && !coches().some(c => c.checked); };   // rien à inclure : pas de rapport vide
    annuler.onclick = () => fermer(null);
    generer.onclick = () => fermer({
      portee: (carte.querySelector('input[name="portee-rapport-general"]:checked') || {}).value || "table",
      // Tout coché = aucun filtre (le serveur prend tout) ; sinon seules les sections cochées partent au serveur.
      sections: (tout => tout.every(c => c.checked) ? [] : tout.filter(c => c.checked).map(c => c.value))(coches()),
    });
    voile.addEventListener("click", (ev) => { if(ev.target === voile) fermer(null); });
    actions.append(annuler, generer);
    carte.append(actions);
    voile.append(carte);
    document.body.append(voile);
  });
}
// Analyse exploratoire par colonne : nombre de valeurs renseignées, de
// cases vides et de valeurs nulles, taux de remplissage. Rendu en cartes
// (grille qui devient une colonne unique en mobile) pour ne jamais être
// coupé/tronqué comme l'ancien résumé en une seule ligne.
function _valeurEstVide(v){
  if(v === null || v === undefined) return true;
  if(Array.isArray(v)) return v.length === 0;
  if(typeof v === "string") return v.trim() === "";
  return false;
}
// Contexte de la dernière analyse exploratoire affichée : sert aux boutons « Lister » des cartes (lignes vides / nulles / aberrantes d'un champ).
// `lignes` = lignes ANALYSÉES (hors liste noire) ; `noires` = lignes de la liste noire (écartées de toutes les analyses, jamais supprimées).
let _statsContexte = {champs: [], lignes: [], noires: [], slug: ""};
// Champs numériques : seuls ceux-là ont des valeurs aberrantes.
const _TYPES_NUMERIQUES = new Set(["nombre", "decimal", "pourcentage", "montant"]);
function _estChampNumerique(c){ return _TYPES_NUMERIQUES.has(String(c.type_champ || c.type || "").toLowerCase()); }
function _nombreOuNull(v){
  if(v === null || v === undefined || v === "" || typeof v === "boolean") return null;
  const n = typeof v === "number" ? v : Number(String(v).replace(",", ".").trim());
  return Number.isFinite(n) ? n : null;
}
// Quantile par interpolation linéaire sur un tableau trié.
function _quantileTrie(tries, q){
  const pos = (tries.length - 1) * q, bas = Math.floor(pos), haut = Math.ceil(pos);
  return tries[bas] + (tries[haut] - tries[bas]) * (pos - bas);
}
// Valeurs aberrantes DANS LES DEUX SENS (règle de Tukey : au-delà de Q1 − 1,5·IQR ou de Q3 + 1,5·IQR).
// Renvoie {bas: [lignes], haut: [lignes], borneBasse, borneHaute} ; vide s'il y a moins de 4 valeurs numériques ou si l'écart interquartile est nul.
function _calculerAberrantes(champ, lignes){
  const vide = {bas: [], haut: [], borneBasse: null, borneHaute: null};
  const valeurs = [];
  for(const l of lignes){ const n = _nombreOuNull(l[champ.slug]); if(n !== null) valeurs.push(n); }
  if(valeurs.length < 4) return vide;
  const tries = valeurs.slice().sort((a, b) => a - b);
  const q1 = _quantileTrie(tries, 0.25), q3 = _quantileTrie(tries, 0.75), iqr = q3 - q1;
  if(!(iqr > 0)) return vide;
  const borneBasse = q1 - 1.5 * iqr, borneHaute = q3 + 1.5 * iqr;
  const bas = [], haut = [];
  for(const l of lignes){
    const n = _nombreOuNull(l[champ.slug]);
    if(n === null) continue;
    if(n < borneBasse) bas.push(l); else if(n > borneHaute) haut.push(l);
  }
  return {bas, haut, borneBasse, borneHaute};
}
function _formaterBorne(n){ return n === null ? "" : String(Math.round(n * 1e6) / 1e6); }
function construireStatsColonnes(champs, toutesLignes, tableSlug){
  // Liste noire : ces lignes restent dans la table mais sont virtuellement écartées de toutes les analyses.
  const noires = toutesLignes.filter(l => l._liste_noire);
  const lignes = toutesLignes.filter(l => !l._liste_noire);
  _statsContexte = {champs, lignes, noires, slug: tableSlug || ""};
  const total = lignes.length;
  const cartes = champs.map(c => {
    let vides = 0, nulles = 0;
    for(const ligne of lignes){
      const v = ligne[c.slug];
      if(v === null || v === undefined) nulles++;
      if(_valeurEstVide(v)) vides++;
    }
    const remplies = total - vides;
    const pctRemplissage = total > 0 ? Math.round((remplies / total) * 100) : 0;
    const aberrantes = _estChampNumerique(c) ? _calculerAberrantes(c, lignes) : null;
    const bornes = aberrantes && aberrantes.borneBasse !== null ? tj("engine/static/engine/js/index_1.js:aberr_bornes", {v1: _formaterBorne(aberrantes.borneBasse), v2: _formaterBorne(aberrantes.borneHaute)}) : "";
    const boutonAberr = (sens, n) => n > 0 ? `<button type="button" class="btn-lister-vides" data-col="${escapeHtml(c.slug)}" data-sens="${sens}" style="margin-top:4px;font-size:12px;padding:3px 8px" title="${escapeHtml((sens === "bas" ? tj("engine/static/engine/js/index_1.js:aberr_lister_titre_bas", {v1: c.nom}) : tj("engine/static/engine/js/index_1.js:aberr_lister_titre_haut", {v1: c.nom})))}">${tj("engine/static/engine/js/index_1.js:vides_lister", {v1: n})}</button>` : "";
    const apparence = window.GYApparence ? window.GYApparence.styleCarte(tableSlug, c.slug) : "";   // couleurs choisies pour cette carte (apparence_tables.js)
    return `<div class="stat-colonne-carte" data-col="${escapeHtml(c.slug)}"${apparence ? ` style="${escapeHtml(apparence)}"` : ""}>
      ${window.GYApparence ? window.GYApparence.boutonCarte(c.slug) : ""}
      <div class="nom-col">${escapeHtml(c.nom)}</div>
      <div class="type-col">${escapeHtml(c.type)}</div>
      <div class="ligne-stat"><span>${tj("engine/static/engine/js/index_1.js:966:38")}</span><b>${remplies} / ${total}</b></div>
      <div class="ligne-stat"><span>${tj("engine/static/engine/js/index_1.js:1140:37")}</span><b>${vides}</b></div>
      <div class="ligne-stat"><span>${tj("engine/static/engine/js/index_1.js:968:38")}</span><b>${nulles}</b></div>
      <div class="ligne-stat"><span>${tj("engine/static/engine/js/index_1.js:1142:37")}</span><b>${pctRemplissage}%</b></div>
      <div class="barre-remplissage"><div class="barre-remplissage-valeur" style="width:${pctRemplissage}%"></div></div>
      ${aberrantes ? `<div class="ligne-stat" style="margin-top:6px"><span>${tj("engine/static/engine/js/index_1.js:aberr_basses")}</span><b>${aberrantes.bas.length}</b></div>
      <div class="ligne-stat"><span>${tj("engine/static/engine/js/index_1.js:aberr_hautes")}</span><b>${aberrantes.haut.length}</b></div>
      ${bornes ? `<div style="font-size:11px;opacity:.7">${escapeHtml(bornes)}</div>` : ""}
      ${boutonAberr("bas", aberrantes.bas.length)}${boutonAberr("haut", aberrantes.haut.length)}` : ""}
      ${_termesEligible(c) ? `<button type="button" class="btn-termes-colonne" data-col="${escapeHtml(c.slug)}" style="margin-top:8px;font-size:12px;padding:3px 8px" title="${escapeHtml(tj("engine/static/engine/js/index_1.js:termes_bouton_titre", {v1: c.nom}))}">${tj("engine/static/engine/js/index_1.js:termes_bouton")}</button>` : ""}
      ${vides > 0 ? `<button type="button" class="btn-lister-vides" data-col="${escapeHtml(c.slug)}" style="margin-top:8px;font-size:12px;padding:3px 8px" title="${escapeHtml(tj("engine/static/engine/js/index_1.js:vides_lister_titre", {v1: c.nom}))}">${tj("engine/static/engine/js/index_1.js:vides_lister", {v1: vides})}</button>` : ""}
    </div>`;
  }).join("");
  const barreNoire = noires.length ? `<div style="grid-column:1/-1"><button type="button" class="btn-liste-noire" style="font-size:12px;padding:3px 8px" title="${escapeHtml(tj("engine/static/engine/js/index_1.js:ln_ouvrir_titre"))}">${tj("engine/static/engine/js/index_1.js:ln_ouvrir", {v1: noires.length})}</button></div>` : "";

  return `<div class="carte" id="carte-stats-colonnes">
    <div class="stats-colonnes-entete" id="toggle-stats-colonnes">
      <span class="titre-stats">${tj("engine/static/engine/js/index_1.js:1149:33", {v1: total, v2: champs.length})}</span>
      <span class="chevron">▶</span>
    </div>
    <div class="grille-stats-colonnes" id="grille-stats-colonnes" data-table="${escapeHtml(tableSlug || "")}" style="display:none">${barreNoire}${cartes}</div>
  </div>`;
}
// Fenêtre des listes de l'analyse exploratoire. Modes : « vides » (champ vide ou nul), « bas » / « haut » (valeurs aberrantes basses / hautes),
// « noire » (lignes de la liste noire). Chaque ligne a une CASE À COCHER : cocher puis « Ajouter à la liste noire » écarte les lignes de toutes
// les analyses SANS les supprimer (la suppression reste possible, séparément). En mode « noire », la même case sert à les remettre dans les analyses.
function _ouvrirListeVides(slugChamp, mode){
  mode = mode || "vides";
  const {champs, lignes, noires, slug} = _statsContexte;
  const champ = champs.find(c => c.slug === slugChamp);
  const table = tables.find(t => t.slug === slug);
  if(!table || (!champ && mode !== "noire")) return;
  const rangs = new Map(); lignes.concat(noires).forEach((l, i) => rangs.set(l, i + 1));   // numéro « # » = position dans la page de la table
  let concernees;
  if(mode === "noire") concernees = noires.filter(l => l._id != null);
  else if(mode === "bas") concernees = _calculerAberrantes(champ, lignes).bas.filter(l => l._id != null);
  else if(mode === "haut") concernees = _calculerAberrantes(champ, lignes).haut.filter(l => l._id != null);
  else concernees = lignes.filter(l => _valeurEstVide(l[slugChamp]) && l._id != null);
  const autres = champs.filter(c => !champ || c.slug !== slugChamp).slice(0, 4);
  const coches = new Set();

  const voile = document.createElement("div");
  voile.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:10000;display:flex;align-items:center;justify-content:center;padding:16px";
  const carte = document.createElement("div");
  carte.style.cssText = "background:var(--card,#101820);color:var(--text,#fff);border:1px solid var(--border,#2a2a2a);border-radius:10px;padding:16px;max-width:760px;width:100%;max-height:85vh;display:flex;flex-direction:column;gap:10px";
  const titre = document.createElement("div");
  titre.style.cssText = "font-weight:600";
  const aide = document.createElement("div");
  aide.style.cssText = "font-size:12px;opacity:.75";
  const toutCocher = document.createElement("label");
  toutCocher.style.cssText = "display:flex;gap:8px;align-items:center;font-size:12px;cursor:pointer";
  const caseTout = document.createElement("input"); caseTout.type = "checkbox";
  const libTout = document.createElement("span"); libTout.textContent = tj("engine/static/engine/js/index_1.js:ln_tout_cocher");
  toutCocher.append(caseTout, libTout);
  const liste = document.createElement("div");
  liste.style.cssText = "overflow:auto;display:flex;flex-direction:column;gap:4px;min-height:60px";
  const actions = document.createElement("div");
  actions.style.cssText = "display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap";
  const fermer = document.createElement("button"); fermer.type = "button"; fermer.textContent = tj("engine/static/engine/js/index_1.js:vides_fermer");
  const btnNoire = document.createElement("button"); btnNoire.type = "button";
  const toutSupprimer = document.createElement("button"); toutSupprimer.type = "button"; toutSupprimer.className = "danger";
  if(mode === "noire") actions.append(fermer, btnNoire);
  else actions.append(fermer, btnNoire, toutSupprimer);
  carte.append(titre, aide, toutCocher, liste, actions);
  voile.append(carte);

  const fin = () => voile.remove();
  const majTitre = () => {
    if(mode === "noire"){
      titre.textContent = tj("engine/static/engine/js/index_1.js:ln_titre", {v1: concernees.length});
      aide.textContent = tj("engine/static/engine/js/index_1.js:ln_aide_noire");
      btnNoire.textContent = tj("engine/static/engine/js/index_1.js:ln_retirer", {v1: coches.size});
    } else {
      titre.textContent = mode === "bas" ? tj("engine/static/engine/js/index_1.js:aberr_titre_bas", {v1: champ.nom, v2: concernees.length})
        : mode === "haut" ? tj("engine/static/engine/js/index_1.js:aberr_titre_haut", {v1: champ.nom, v2: concernees.length})
        : tj("engine/static/engine/js/index_1.js:vides_titre", {v1: champ.nom, v2: concernees.length});
      aide.textContent = tj("engine/static/engine/js/index_1.js:ln_aide");
      btnNoire.textContent = tj("engine/static/engine/js/index_1.js:ln_ajouter", {v1: coches.size});
      toutSupprimer.textContent = tj("engine/static/engine/js/index_1.js:vides_supprimer_tout", {v1: concernees.length});
      toutSupprimer.disabled = !concernees.length;
    }
    btnNoire.disabled = !coches.size;
    toutCocher.style.display = concernees.length ? "flex" : "none";
    caseTout.checked = concernees.length > 0 && coches.size === concernees.length;
  };
  // Suppression par lots de 500 (limite de l'API), puis rechargement de la page courante de la table.
  const supprimer = async (ids) => {
    let supprimees = 0;
    try {
      for(let i = 0; i < ids.length; i += 500){
        const res = await api(`/api/tables/${table.id}/lignes/supprimer-lot/`, {method:"POST", body: JSON.stringify({ids: ids.slice(i, i + 500)})});
        supprimees += res.supprimees || 0;
      }
    } catch(e) { /* déjà notifié par api() */ }
    if(supprimees) notifierSucces(tj("engine/static/engine/js/index_1.js:vides_supprime", {v1: supprimees}));
    return supprimees;
  };
  // Liste noire par lots de 500 : actif=true ajoute, actif=false retire. Les lignes restent dans la table.
  const changerListeNoire = async (ids, actif) => {
    let modifiees = 0;
    try {
      for(let i = 0; i < ids.length; i += 500){
        const res = await api(`/api/tables/${table.id}/lignes/liste-noire/`, {method:"POST", body: JSON.stringify({ids: ids.slice(i, i + 500), actif})});
        modifiees += res.modifiees || 0;
      }
    } catch(e) { /* déjà notifié par api() */ }
    if(modifiees) notifierSucces(tj(actif ? "engine/static/engine/js/index_1.js:ln_ajoutees" : "engine/static/engine/js/index_1.js:ln_retirees", {v1: modifiees}));
    return modifiees;
  };
  const rendre = () => {
    liste.innerHTML = "";
    majTitre();
    if(!concernees.length){
      const vide = document.createElement("div"); vide.style.opacity = ".7"; vide.textContent = mode === "noire" ? tj("engine/static/engine/js/index_1.js:ln_vide") : tj("engine/static/engine/js/index_1.js:vides_aucune");
      liste.append(vide); return;
    }
    concernees.forEach((l) => {
      const ligne = document.createElement("div");
      ligne.style.cssText = "display:flex;gap:10px;align-items:center;justify-content:space-between;padding:4px 6px;border:1px solid var(--border,#2a2a2a);border-radius:6px;font-size:12px";
      const coche = document.createElement("input"); coche.type = "checkbox"; coche.style.flex = "none"; coche.checked = coches.has(l._id);
      coche.title = tj("engine/static/engine/js/index_1.js:ln_coche_titre");
      coche.onchange = () => { if(coche.checked) coches.add(l._id); else coches.delete(l._id); majTitre(); };
      const lignesAffichees = (mode === "bas" || mode === "haut") ? [champ].concat(autres) : autres;
      const resume = lignesAffichees.map(c => {
        const v = l[c.slug];
        return c.nom + " : " + (_valeurEstVide(v) ? tj("engine/static/engine/js/index_1.js:vides_valeur_vide") : (typeof v === "object" ? JSON.stringify(v) : String(v)));
      }).join(" · ");
      const texte = document.createElement("span");
      texte.style.cssText = "overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;flex:1";
      texte.textContent = tj("engine/static/engine/js/index_1.js:vides_ligne_index", {v1: l._id, v2: rangs.get(l)}) + (resume ? " — " + resume : "");
      texte.title = texte.textContent;
      ligne.append(coche, texte);
      if(mode !== "noire"){
        const btn = document.createElement("button"); btn.type = "button"; btn.className = "danger"; btn.style.cssText = "font-size:12px;padding:2px 8px;flex:none";
        btn.textContent = tj("engine/static/engine/js/index_1.js:vides_supprimer_ligne");
        btn.onclick = async () => {
          if(!await GY.confirmer(tj("engine/static/engine/js/index_1.js:vides_confirm_une"))) return;
          if(await supprimer([l._id])){ concernees.splice(concernees.indexOf(l), 1); coches.delete(l._id); rendre(); _rechargerApresSuppression(slug); }
        };
        ligne.append(btn);
      }
      liste.append(ligne);
    });
  };
  caseTout.onchange = () => { coches.clear(); if(caseTout.checked) concernees.forEach(l => coches.add(l._id)); rendre(); };
  btnNoire.onclick = async () => {
    if(!coches.size) return;
    if(await changerListeNoire(Array.from(coches), mode !== "noire")){ fin(); _rechargerApresSuppression(slug); }   // la page est relue : les analyses ne comptent plus (ou recomptent) ces lignes
  };
  toutSupprimer.onclick = async () => {
    if(!concernees.length) return;
    const confirmation = mode === "bas" ? tj("engine/static/engine/js/index_1.js:aberr_confirm_tout_bas", {v1: concernees.length, v2: champ.nom})
      : mode === "haut" ? tj("engine/static/engine/js/index_1.js:aberr_confirm_tout_haut", {v1: concernees.length, v2: champ.nom})
      : tj("engine/static/engine/js/index_1.js:vides_confirm_tout", {v1: concernees.length, v2: champ.nom});
    if(!await GY.confirmer(confirmation)) return;
    if(await supprimer(concernees.map(l => l._id))){ fin(); _rechargerApresSuppression(slug); }
  };
  fermer.onclick = fin;
  voile.addEventListener("click", (ev) => { if(ev.target === voile) fin(); });
  rendre();
  document.body.append(voile);
}
function _rechargerApresSuppression(slug){
  const e = _paginationEtat[slug];
  afficherTable(slug, (e && e.page) || 1); rafraichirTables();
}
// Un seul écouteur pour tous les boutons « Lister » des cartes (les cartes sont redessinées à chaque affichage de la table).
document.addEventListener("click", (ev) => {
  const b = ev.target.closest && ev.target.closest(".btn-lister-vides");
  if(b){ _ouvrirListeVides(b.dataset.col, b.dataset.sens || "vides"); return; }
  const n = ev.target.closest && ev.target.closest(".btn-liste-noire");
  if(n) _ouvrirListeVides("", "noire");
  const tb = ev.target.closest && ev.target.closest(".btn-termes-colonne");
  if(tb) _ouvrirTermes(tb.dataset.col);
});
// Statistiques par terme d'une colonne (variantes d'un même mot : bz, BZ, Brazzavile…) : GET .../termes/, lu dans un cache fichier côté serveur
// (engine/termes_colonne.py). Cocher des termes puis « Fusionner » ouvre la transformation « fusionner » avec ces termes déjà saisis.
const _TYPES_SANS_TERMES = new Set([...TYPES_FICHIER, "relation", "choix_multiple", "formule", "constante", "horodatage", "id_client", "vecteur", "matrice", "json"]);
function _termesEligible(c){ return !_TYPES_SANS_TERMES.has(c.type) && !_estChampNumerique(c); }
async function _ouvrirTermes(slugChamp, recalculer){
  const table = tables.find(t => t.slug === _statsContexte.slug), champ = table && table.champs.find(c => c.slug === slugChamp);
  if(!champ) return;
  let d;
  try { d = await api(`/api/tables/${table.id}/champs/${champ.id}/termes/${recalculer ? "?recalculer=1" : ""}`); } catch(err) { return; }   // api() a déjà signalé l'erreur
  document.getElementById("modal-termes-fond")?.remove();
  const fond = document.createElement("div");
  fond.id = "modal-termes-fond";
  fond.style.cssText = "position:fixed;inset:0;background:color-mix(in srgb,var(--voile,#000000) 75%,transparent);display:flex;align-items:center;justify-content:center;z-index:2000;padding:16px";
  const K = "engine/static/engine/js/index_1.js:", coches = new Set();
  fond.innerHTML = `<div style="width:640px;max-width:100%;max-height:92vh;display:flex;flex-direction:column;background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:18px">
    <h2 style="margin:0 0 4px;font-size:15px">${tj(K + "termes_titre", {v1: escapeHtml(champ.nom)})}</h2>
    <p style="margin:0 0 8px;font-size:12px;color:var(--muted)">${tj(K + "termes_resume", {v1: d.distincts, v2: d.total, v3: d.vides})} · ${d.en_cache ? tj(K + "termes_cache", {v1: escapeHtml(d.calcule_le || "")}) : tj(K + "termes_frais")}${d.tronque ? ` · ${tj(K + "termes_tronque", {v1: d.termes.length})}` : ""}</p>
    <input id="termes-filtre" type="text" placeholder="${escapeHtml(tj(K + "termes_filtrer"))}" style="padding:6px 8px;font-size:13px;width:100%;box-sizing:border-box;margin-bottom:8px" autocomplete="off">
    <div style="overflow:auto;flex:1;min-height:120px"><table style="width:100%;font-size:12px;border-collapse:collapse"><thead><tr><th style="width:28px"></th><th style="text-align:left;padding:3px 6px">${tj(K + "termes_col_terme")}</th><th style="text-align:right;padding:3px 6px">${tj(K + "termes_col_n")}</th><th style="text-align:right;padding:3px 6px">%</th></tr></thead><tbody id="termes-corps"></tbody></table></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;flex-wrap:wrap">
      <button id="termes-recalculer" type="button">${tj(K + "termes_recalculer")}</button>
      <button id="termes-fermer" type="button">${tj("engine/static/engine/js/index_1.js:2222:52")}</button>
      <button id="termes-fusionner" type="button" class="principal" disabled></button>
    </div></div>`;
  document.body.appendChild(fond);
  const el = (id) => fond.querySelector("#" + id), fermer = () => fond.remove();
  const maj = () => { const b = el("termes-fusionner"); b.disabled = !coches.size; b.textContent = tj(K + "termes_fusionner", {v1: coches.size}); };
  const dessiner = () => {
    const f = el("termes-filtre").value.trim().toLowerCase();
    el("termes-corps").innerHTML = d.termes.filter(x => !f || x.terme.toLowerCase().includes(f)).map(x =>
      `<tr><td style="padding:3px 6px;border-top:1px solid var(--border)"><input type="checkbox" data-terme="${escapeHtml(x.terme)}"${coches.has(x.terme) ? " checked" : ""}></td><td style="padding:3px 6px;border-top:1px solid var(--border);overflow-wrap:anywhere">${escapeHtml(x.terme)}</td><td style="padding:3px 6px;border-top:1px solid var(--border);text-align:right">${x.n}</td><td style="padding:3px 6px;border-top:1px solid var(--border);text-align:right">${d.total ? Math.round(x.n / d.total * 1000) / 10 : 0}</td></tr>`).join("")
      || `<tr><td colspan="4" style="padding:8px;color:var(--muted)">${tj(K + "termes_aucun")}</td></tr>`;
  };
  el("termes-filtre").addEventListener("input", dessiner);
  el("termes-corps").addEventListener("change", (ev) => { const t = ev.target.dataset && ev.target.dataset.terme; if(t === undefined) return; ev.target.checked ? coches.add(t) : coches.delete(t); maj(); });
  el("termes-fermer").onclick = fermer;
  el("termes-recalculer").onclick = () => { fermer(); _ouvrirTermes(slugChamp, true); };
  el("termes-fusionner").onclick = () => { const termes = [...coches]; fermer(); ouvrirModalTransformation(table, champ, {termes}); };
  fond.addEventListener("mousedown", (ev) => { if(ev.target === fond) fermer(); });
  dessiner(); maj();
}
// Vue Données
// ── État pagination par table ─────────────────────────────────────────────
const _paginationEtat = {};  // slug -> {page, taille, total, pages}
// Recherche / tri / filtres de la grille, appliqués CÔTÉ SERVEUR sur toute la table (engine/grille.py) :
// slug -> {q, tri, sens, filtres:{slug: expression}}. Voir aussi static/engine/js/grille_tableur.js.
const _grilleCriteres = {};
// Sous-onglet ouvert de la vue Données, par table : formulaire · zone de recherche · analyse exploratoire · tableau.
const _sousOngletDonnees = {};
function _criteresGrille(slug){
  return _grilleCriteres[slug] || (_grilleCriteres[slug] = {q:"", tri:"", sens:"", filtres:{}});
}
function _requeteCriteres(slug){
  const c = _criteresGrille(slug), p = new URLSearchParams();
  if(c.q) p.set("q", c.q);
  if(c.tri){ p.set("tri", c.tri); p.set("sens", c.sens || "asc"); }
  for(const [k, v] of Object.entries(c.filtres)) if(v) p.set("f_" + k, v);
  return p.toString();
}
function _criteresActifs(slug){
  const c = _criteresGrille(slug);
  return !!(c.q || c.tri || Object.values(c.filtres).some(Boolean));
}

async function afficherTable(slug, page){
  modeVue = "table"; tableActive = slug; memoriserContexteAccueil();

  // Rafraîchir la liste des tables pour avoir les champs à jour
  await rafraichirTables();

  const table = tables.find(t => t.slug === slug);
  if(!table){ return; }

  // Pagination : 200 lignes par défaut
  const etat = _paginationEtat[slug] || {page:1, taille:200};
  if(page !== undefined) etat.page = page;
  const _crit = _criteresGrille(slug);
  const _qs = _requeteCriteres(slug);
  const data = await api(`/api/tables/${table.id}/lignes/?page=${etat.page}&taille=${etat.taille}${_qs ? "&" + _qs : ""}`);
  // Vue triée / filtrée / recherchée : l'ordre affiché n'est plus chronologique, or les fusions de cellules
  // sont repérées par rang chronologique -> on ne les affiche (ni ne les crée) que sur la vue par défaut.
  const _vueReordonnee = _criteresActifs(slug);
  const pag = data.pagination || {};
  _paginationEtat[slug] = {
    page:   pag.page  || etat.page,
    taille: pag.taille|| etat.taille,
    total:  pag.total || 0,
    pages:  pag.pages || 1,
  };
  // Fusions de cellules (voir FusionCelluleTable) : lignes[] est trié
  // -cree_le (le plus récent d'abord) alors que ligne_debut/ligne_fin sont
  // chronologiques ascendants (0 = plus ancienne) — _dispIndexDepuisFusion
  // convertit l'un vers l'autre à partir du total de lignes.
  const _fusionsData = await api(`/api/tables/${table.id}/fusions/`);
  const fusions = (_vueReordonnee || table.groupe_id) ? [] : (_fusionsData.fusions || []);   // fusions repérées par rang dans UNE table : pas dans la table générale d'un groupe
  const _offsetPage = (( _paginationEtat[slug].page || 1) - 1) * (_paginationEtat[slug].taille || 200);
  const _totalLignes = _paginationEtat[slug].total || data.lignes.length;
  const _dispDepuisChrono = (c) => _totalLignes - 1 - c;
  const couvertes = new Set();   // "dispIndex-col" -> cellule cachée (sous une ancre)
  const ancres = {};             // "dispIndex-col" -> fusion (cellule ancre, coin haut-gauche affiché)
  _rectsFusion = [];
  // Colonnes dont UNE cellule a une autre nature que la colonne (cellule seule retypée) : repérées dans l'en-tête.
  const _chsTable = table.champs || [];
  const _colsRetypees = new Set(fusions.filter(f => f.ligne_debut === f.ligne_fin && f.col_debut === f.col_fin
    && _chsTable[f.col_debut] && f.type_affiche !== (_chsTable[f.col_debut].type_champ || _chsTable[f.col_debut].type)).map(f => f.col_debut));
  for(const f of fusions){
    const dispDebut = _dispDepuisChrono(f.ligne_fin);   // le plus récent des deux -> index d'affichage le plus petit
    const dispFin   = _dispDepuisChrono(f.ligne_debut);
    _rectsFusion.push({r1: dispDebut, r2: dispFin, c1: f.col_debut, c2: f.col_fin});
    for(let d = dispDebut; d <= dispFin; d++){
      for(let col = f.col_debut; col <= f.col_fin; col++){
        if(d === dispDebut && col === f.col_debut) ancres[`${d}-${col}`] = f;
        else couvertes.add(`${d}-${col}`);
      }
    }
  }

  { const _et = document.getElementById("entete-titre"); if(_et) _et.textContent = table.nom; }  // textContent : pas d'échappement HTML (sinon « &amp; » affiché)

  rendreActionsTable(table, slug);

  const champs = table.champs;
  let html = "";
  // Formulaire d'ajout — disponible pour toutes les tables (importées ou natives)
  // Pliant comme les cartes du dashboard, replié par défaut.
  // Table d'un groupe : l'ajout de lignes se fait uniquement dans la DERNIÈRE table du groupe.
  const _derniereGroupe = table.groupe_id && table.peut_ajouter_lignes === false
    ? tables.filter(t => t.groupe_id === table.groupe_id).sort((a, b) => (b.partie_index || 0) - (a.partie_index || 0))[0] : null;
  // Un groupe s'ouvre comme UNE table : la grille couvre toutes ses parties, les nouvelles lignes vont dans la dernière (serveur).
  const _partiesGroupe = table.groupe_id ? tables.filter(t => t.groupe_id === table.groupe_id).sort((a, b) => (a.partie_index || 0) - (b.partie_index || 0)) : [];
  if(table.groupe_id){
    const _fin = _partiesGroupe[_partiesGroupe.length - 1];
    html += `<div class="carte"><div class="corps-carte" style="font-size:12px;color:var(--muted)">${escapeHtml(tj("engine/static/engine/js/index_1.js:groupe_table_ouverte", {v1: table.groupe_nom || "", v2: _partiesGroupe.length, v3: _partiesGroupe.reduce((n, t) => n + (t.nombre_lignes || 0), 0), v4: _fin ? " (" + _fin.nom + ")" : ""}))}</div></div>`;
  }
  if(table.peut_ajouter_lignes === false && !table.groupe_id){
    html += `<div class="carte"><div class="corps-carte" style="font-size:12px;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:1068:101")} ${escapeHtml(table.groupe_nom || "")} ${tj("engine/static/engine/js/index_1.js:1068:185")}${_derniereGroupe ? ` <a href="#" id="lien-derniere-groupe" class="btn petit">${tj("engine/static/engine/js/index_1.js:1068:336")}${escapeHtml(_derniereGroupe.nom)})</a>` : ""}</div></div>`;
  } else {
    html += '<div class="carte">';
    html += `<div class="entete-carte"><span class="titre-analyse">${tj("engine/static/engine/js/index_1.js:1071:69")}${table.provient_fichier ? tj("engine/static/engine/js/index_1.js:1071:113") : ''}</span><button class="btn-reduire" title="${tj("engine/static/engine/js/index_1.js:1071:254")}">▸</button></div>`;
    html += `<div class="corps-carte gyf-hote"><div class="form-titre">${escapeHtml(table.nom)}</div><div class="form-desc">${escapeHtml(table.description || "")}</div><div class="form-grille gyf-champs">`;
    for(const c of champs){
      html += `<div class="champ-item groupe" data-champ-slug="${escapeHtml(c.slug)}"><label${_styleLabelChamp(c)}>${escapeHtml(c.nom)}${c.obligatoire ? ' <span class="obligatoire">*</span>' : ''}${_btnCouleurChamp(table, c)}</label>${champVersInput(c)}</div>`;
    }
    html += tj("engine/static/engine/js/index_1.js:1076:14");
  }
  const _finForm = html.length;   // fin du sous-onglet « Formulaire »

  const _nbFiltres = Object.values(_crit.filtres).filter(Boolean).length;
  html += `<div class="barre-recherche-lignes barre-tableur">
    <input id="recherche-lignes" type="search" placeholder="${tj("engine/static/engine/js/index_1.js:a:1158:60")}" autocomplete="off" value="${escapeHtml(_crit.q)}">
    <span class="compteur-recherche" id="compteur-recherche">${_vueReordonnee ? tj("engine/static/engine/js/index_1.js:1082:82", {v1: (data.pagination||{}).total ?? data.lignes.length, v2: _nbFiltres ? ` · ${_nbFiltres} filtre(s)` : ""}) : ""}</span>
    ${_vueReordonnee ? tj("engine/static/engine/js/index_1.js:1083:25") : ""}
    <button id="btn-filtres-colonnes" type="button" class="${_nbFiltres ? "actif" : ""}" title="${tj("engine/static/engine/js/index_1.js:a:1161:96")}">${tj("engine/static/engine/js/index_1.js:1266:152")}</button>
    <button id="btn-ligne-vide" type="button" title="${tj("engine/static/engine/js/index_1.js:1085:55")}">${tj("engine/static/engine/js/index_1.js:1085:124")}</button>
    <button id="btn-annuler-grille" type="button" title="${tj("engine/static/engine/js/index_1.js:1086:59")}">↶ ${tj("engine/static/engine/js/grille_tableur.js:tactile:annuler")}</button>
    <button id="btn-refaire-grille" type="button" title="${tj("engine/static/engine/js/index_1.js:1087:59")}" disabled>↷ ${tj("engine/static/engine/js/grille_tableur.js:tactile:refaire")}</button>
    <button id="btn-export-xlsx" type="button" title="${tj("engine/static/engine/js/index_1.js:1088:56")}">${tj("engine/static/engine/js/index_1.js:1270:108")}</button>
    <button id="btn-export-parquet" type="button" title="${tj("export.parquet.titre")}">${tj("engine/static/engine/js/index_1.js:1271:89")}</button>
    ${table.type_stockage === "nosql" ? "" : `<button id="btn-export-csv" type="button" title="${tj("export.csv.titre")}">${tj("export.csv.libelle")}</button>`}
    <button id="btn-export-json" type="button" title="${tj("export.json.titre")}">${tj("export.json.libelle")}</button>
    ${table.est_table_membres ? tj("engine/static/engine/js/index_1.js:1092:34") : ""}
  </div>`;
  const _finRech = html.length;   // fin du sous-onglet « Zone de recherche »

  html += construireStatsColonnes(champs, data.lignes, slug);
  const _finStats = html.length;  // fin du sous-onglet « Analyse exploratoire » ; le reste est le « Tableau »

  // Tableau de données — pliant lui aussi, mais ouvert par défaut (c'est le
  // contenu principal de la vue).
  // ── Contrôles de pagination ─────────────────────────────────────────────
  const _pag = _paginationEtat[slug] || {};
  const _pagTotal = _pag.total || data.lignes.length;
  const _pagPages = _pag.pages || 1;
  const _pagPage  = _pag.page  || 1;
  let pagHtml = '';
  if(_pagPages > 1){
    pagHtml = `<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:6px 0">
      <button onclick="afficherTable('${slug}',1)" ${_pagPage<=1?'disabled':''} style="padding:3px 8px;font-size:12px">«</button>
      <button onclick="afficherTable('${slug}',${_pagPage-1})" ${_pagPage<=1?'disabled':''} style="padding:3px 8px;font-size:12px">‹</button>
      <span style="font-size:12px;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:1294:55", {v1: _pagPage, v2: _pagPages, v3: _pagTotal, v4: tj("engine/static/engine/js/index_1.js:1109:102")})}</span>
      <button onclick="afficherTable('${slug}',${_pagPage+1})" ${_pagPage>=_pagPages?'disabled':''} style="padding:3px 8px;font-size:12px">›</button>
      <button onclick="afficherTable('${slug}',${_pagPages})" ${_pagPage>=_pagPages?'disabled':''} style="padding:3px 8px;font-size:12px">»</button>
      <select onchange="(_paginationEtat['${slug}']={page:1,taille:+this.value,total:0,pages:1},afficherTable('${slug}',1))" style="padding:3px;font-size:12px;background:var(--bg);border:1px solid var(--border);color:var(--text)">
        ${[50,100,200,500,1000].map(n=>`<option value="${n}" ${n===(_pag.taille||200)?'selected':''}>${n}/page</option>`).join('')}
      </select>
    </div>`;
  }

  // Ouvert par défaut (c'est le contenu principal de la vue) : replié, il se refermait à chaque tri, filtre
  // ou changement de page puisque la grille est reconstruite.
  html += tj("engine/static/engine/js/index_1.js:1120:12") + _pagTotal + tj("engine/static/engine/js/index_1.js:1120:126");
  html += tj("engine/static/engine/js/index_1.js:1121:12");
  html += '<th class="gy-coin" aria-hidden="true"></th>';   // coin de la colonne des numéros de ligne (rendu « tableur »)
  if(table.est_table_membres && !table.est_table_produits) html += tj("engine/static/engine/js/index_1.js:1122:40");
  if(table.est_table_membres) html += `<th style="white-space:nowrap">${tj("engine/static/engine/js/index_1.js:272:135")}</th>`;
  for(let colIdx = 0; colIdx < champs.length; colIdx++){
    const c = champs[colIdx];
    let badge = "";
    if(c.type === "relation" && c.relation_vers_id){
      const nomCible = c.relation_vers_nom || c.relation_vers || tj("engine/static/engine/js/index_1.js:1127:67");
      badge = ` <button type="button" class="btn-nav-table-source" data-table-slug="${escapeHtml(c.relation_vers||"")}" title="${tj("engine/static/engine/js/index_1.js:1314:128", {v1: escapeHtml(nomCible)})}" style="font-size:10px;padding:1px 5px;border:1px solid var(--border);border-radius:3px;background:transparent;color:var(--muted);cursor:pointer;vertical-align:middle;white-space:nowrap">↗ ${escapeHtml(nomCible)}</button>`;
    } else if(c.groupe_partage_id && (c.partage_membres||[]).length){
      const noms = (c.partage_membres||[]).map(m => m.table_nom).filter((v,i,a)=>a.indexOf(v)===i);
      badge = ` <button type="button" class="btn-nav-table-source" data-table-slug="${escapeHtml((c.partage_membres[0]||{}).table_slug||"")}" data-partage-membres='${JSON.stringify((c.partage_membres||[]).map(m=>({slug:(m.table_slug||""), nom:m.table_nom})))}' title="${tj("engine/static/engine/js/index_1.js:1317:269", {v1: escapeHtml(noms.join(", "))})}" style="font-size:10px;padding:1px 5px;border:1px solid var(--border);border-radius:3px;background:transparent;color:var(--muted);cursor:pointer;vertical-align:middle;white-space:nowrap">🔗 ${escapeHtml(noms.length===1?noms[0]:noms.length+" tables")}</button>`;
    }
    const eligibleFormule = !TYPES_FICHIER.includes(c.type) && c.type !== "relation" && c.type !== "choix_multiple" && c.type !== "formule" && c.type !== "constante" && c.type !== "horodatage" && c.type !== "id_client";
    const btnFormuleChamp = eligibleFormule ? ` <button type="button" class="btn-formule-champ" data-table-slug="${escapeHtml(slug)}" data-champ-slug="${escapeHtml(c.slug)}" title="${tj("engine/static/engine/js/index_1.js:a:1211:181")}" style="font-size:10px;padding:1px 5px;border:1px solid var(--border);border-radius:3px;background:transparent;color:var(--muted);cursor:pointer;vertical-align:middle">∑</button>` : "";
    // Transformation de colonne par motif (regex) façon Power BI — mêmes colonnes que la modification de cellule, sauf json/tenseurs.
    const eligibleTransfo = eligibleFormule && !["vecteur","matrice","json"].includes(c.type);
    const btnTransfoChamp = eligibleTransfo ? ` <button type=\"button\" class=\"btn-transformer-champ\" data-table-slug=\"${escapeHtml(slug)}\" data-champ-slug=\"${escapeHtml(c.slug)}\" title=\"${tj("engine/static/engine/js/index_1.js:a:1214:194")}\" style=\"font-size:10px;padding:1px 5px;border:1px solid var(--border);border-radius:3px;background:transparent;color:var(--muted);cursor:pointer;vertical-align:middle\">⟲</button>` : "";
    // Couleur de colonne (🎨, reprise dans le PDF) : en-tête en couleur pleine, texte noir ou blanc selon le fond.
    const _styleCouleurEntete = c.couleur ? `;background:${c.couleur};color:${_texteSurCouleur(c.couleur)}` : "";
    const btnCouleurChamp = ` <button type="button" class="btn-couleur-champ" data-table-slug="${escapeHtml(slug)}" data-champ-slug="${escapeHtml(c.slug)}" title="${tj("engine/static/engine/js/index_1.js:a:1217:163")}" style="font-size:10px;padding:1px 5px;border:1px solid var(--border);border-radius:3px;background:transparent;color:${c.couleur ? "inherit" : "var(--muted)"};cursor:pointer;vertical-align:middle">🎨</button>`;
    const _triable = !TYPES_FICHIER.includes(c.type) && !["vecteur","matrice","json"].includes(c.type);
    const _indTri = _crit.tri === c.slug ? (_crit.sens === "desc" ? " ▼" : " ▲") : "";
    const _titreEntete = (_triable ? tj("engine/static/engine/js/index_1.js:1143:39") : "") + tj("engine/static/engine/js/index_1.js:1143:106");
    html += `<th style="white-space:nowrap${_styleCouleurEntete}" data-col-idx="${colIdx}" title="${_titreEntete}"${_triable ? ` class="th-triable${_indTri ? " trie" : ""}" data-tri-slug="${escapeHtml(c.slug)}"` : ""}>${escapeHtml(c.nom)}<span class="indicateur-tri">${_indTri}</span>${_colsRetypees.has(colIdx) ? tj("engine/static/engine/js/index_1.js:1144:316") : ''}${badge}${btnFormuleChamp}${btnTransfoChamp}${btnCouleurChamp}</th>`;
  }
  html += '<th class="col-actions" style="width:90px">' + tj("engine/static/engine/js/index_1.js:1332:55") + '</th>';
  html += '</tr>';
  // Ligne de filtres façon « filtre automatique » d'Excel : « texte » (contient), « =valeur », « >10 »,
  // « <=2026-01-31 », « !=(vide) »… Appliquée côté serveur à toute la table.
  html += `<tr class="ligne-filtres"${(_nbFiltres || _grilleFiltresOuverts[slug]) ? "" : ' hidden'}><th class="gy-coin"></th>` + (table.est_table_membres ? (table.est_table_produits ? '<th></th>' : '<th></th><th></th>') : '');
  for(const c of champs){
    const _filtrable = !TYPES_FICHIER.includes(c.type) && !["vecteur","matrice"].includes(c.type);
    html += `<th>${_filtrable ? `<input type="text" class="filtre-colonne" data-slug="${escapeHtml(c.slug)}" value="${escapeHtml(_crit.filtres[c.slug] || "")}" placeholder="${[...TYPES_NUM_JS,"date","datetime","heure"].includes(c.type) ? tj("engine/static/engine/js/index_1.js:filtre_exemple") : tj("engine/static/engine/js/index_1.js:filtre_contient")}" title="${tj('engine/static/engine/js/index_1.js:filtre_aide')}">` : ""}</th>`;
  }
  html += '<th class="col-actions"></th></tr></thead><tbody>';
  for(let _i = 0; _i < data.lignes.length; _i++){
    const ligne = data.lignes[_i];
    const dispIndex = _offsetPage + _i;
    html += `<tr data-ligne-id="${ligne._id}"${_hexMarque(ligne._couleur) ? ` class="ligne-coloree" style="--lig-fond:${_fondCouleur(_hexMarque(ligne._couleur), _ALPHA_MARQUE)}"` : ""}><th scope="row" class="gy-num">${dispIndex + 1}</th>` + (table.est_table_membres ? (table.est_table_produits ? "" : `<td style="font-family:monospace;color:var(--accent)">${escapeHtml(ligne.code_acces_client||"—")} <button type="button" class="btn-tables-client" style="font-size:11px;padding:2px 6px;margin-left:6px" onclick="ouvrirTablesClientLigne(${table.id}, ${ligne._id})">${tj("engine/static/engine/js/index_1.js:g:tables_client_btn")}</button></td>`) + `<td style="font-family:monospace">${escapeHtml(ligne.matricule||"—")}</td>` : "") + champs.map((c, colIdx) => {
      const cle = `${dispIndex}-${colIdx}`;
      if(couvertes.has(cle)) return "";  // cellule cachée sous une fusion (voir ancre)
      const ancre = ancres[cle];
      if(ancre){
        const nbLignesVisibles = Math.min(_dispDepuisChrono(ancre.ligne_debut) - dispIndex + 1, data.lignes.length - _i);
        const colspan = ancre.col_fin - ancre.col_debut + 1;
        // Contenu typé : résultat évalué pour une formule (erreur signalée en survol), valeur saisie sinon.
        const _estFormule = ancre.type_affiche === "formule";
        const _texteFusion = _estFormule ? (ancre.valeur_calculee || (ancre.erreur_calcul ? "#ERREUR" : "")) : (ancre.valeur_affichee || "");
        const _titreFusion = ancre.erreur_calcul ? tj("engine/static/engine/js/index_1.js:lbl_formule_erreur", {v1: ancre.erreur_calcul}) : tj("engine/static/engine/js/index_1.js:1169:90", {v1: ancre.type_affiche});
        return `<td class="cellule-fusionnee" data-fusion-id="${ancre.id}" data-fusion-type="${escapeHtml(ancre.type_affiche)}" data-fusion-valeur="${escapeHtml(ancre.valeur_affichee || "")}" data-ligne-idx="${dispIndex}" data-col-idx="${colIdx}" rowspan="${nbLignesVisibles}" colspan="${colspan}" title="${escapeHtml(_titreFusion)}"${ancre.erreur_calcul ? ' style="color:var(--texte-erreur,#e07070)"' : ""}>${escapeHtml(_texteFusion)}</td>`;
      }
      const valBrute = ligne[c.slug];
      const label = (c.type === "relation" && ligne[c.slug + "__label"])
        ? ligne[c.slug + "__label"]
        : null;
      const editable = !TYPES_FICHIER.includes(c.type) && c.type !== "relation" && c.type !== "choix_multiple" && c.type !== "formule" && c.type !== "constante" && c.type !== "horodatage" && c.type !== "id_client";
      // Couleur propre à CETTE cellule (ligne._couleurs[slug]) : prioritaire sur celle de la ligne, elle-même prioritaire sur celle de la colonne.
      const _cel = _hexMarque((ligne._couleurs || {})[c.slug]);
      const _clsCellule = [editable ? "cellule-editable" : "", c.couleur ? "cellule-coloree" : "", _cel ? "cellule-coloree-propre" : ""].filter(Boolean).join(" ");
      const _styleCel = [c.couleur ? `--col-fond:${_fondCouleur(c.couleur, "30")}` : "", _cel ? `--cel-fond:${_fondCouleur(_cel, _ALPHA_MARQUE)}` : ""].filter(Boolean).join(";");
      return `<td data-ligne-idx="${dispIndex}" data-col-idx="${colIdx}"${_clsCellule ? ` class="${_clsCellule}"` : ""}${_styleCel ? ` style="${_styleCel}"` : ""}>${valeurCellule(c, valBrute, label)}${editable ? tj("engine/static/engine/js/index_1.js:1181:214") : ''}</td>`;
    }).join("");
    html += `<td class="col-actions" style="white-space:nowrap"><button class="btn-modifier-ligne" title="${tj("engine/static/engine/js/index_1.js:1183:88")}" style="padding:3px 6px;font-size:12px">✏️</button>
       <button class="btn-partager-ligne" title="${tj("engine/static/engine/js/index_1.js:1184:51")}" style="padding:3px 6px;font-size:12px">🔗</button>
       <button class="btn-couleur-ligne" title="${tj("engine/static/engine/js/index_1.js:1185:50")}" style="padding:3px 6px;font-size:12px">🎨</button>
       <button class="btn-pdf-ligne" title="${tj("engine/static/engine/js/index_1.js:1186:46")}" style="padding:3px 6px;font-size:12px">📄</button>
       <button class="btn-fiche-ligne" title="${tj("engine/static/engine/js/index_1.js:1373:47")}" style="padding:3px 6px;font-size:12px">🗂</button>
       <button class="danger btn-supprimer-ligne" title="${tj("engine/static/engine/js/index_1.js:1188:59")}" style="padding:3px 6px;font-size:12px">🗑</button></td>`;
    html += '</tr>';
  }
  html += '</tbody></table></div>';
  html += pagHtml;  // contrôles de pagination (vide si 1 seule page)
  html += '</div></div>';

  // Quatre sous-onglets dans le même onglet Données : le contenu est découpé aux repères posés plus haut (aucun autre changement).
  const _SOUS = ["formulaire", "recherche", "analyse", "tableau"];
  let _sousActif = _sousOngletDonnees[slug];
  if(!_SOUS.includes(_sousActif)) _sousActif = "tableau";
  const _libSous = {
    formulaire: tj("engine/static/engine/js/index_1.js:g:donnees_sous_formulaire"),
    recherche:  tj("engine/static/engine/js/index_1.js:g:donnees_sous_recherche"),
    analyse:    tj("engine/static/engine/js/index_1.js:g:donnees_sous_analyse"),
    tableau:    tj("engine/static/engine/js/index_1.js:g:donnees_sous_tableau"),
  };
  const _partiesSous = {formulaire: html.slice(0, _finForm), recherche: html.slice(_finForm, _finRech), analyse: html.slice(_finRech, _finStats), tableau: html.slice(_finStats)};
  html = `<div class="onglets-vue" id="onglets-donnees" style="margin-bottom:10px">`
    + _SOUS.map(k => `<button type="button" class="onglet-vue${k === _sousActif ? " actif" : ""}" data-sous="${k}">${escapeHtml(_libSous[k])}</button>`).join("")
    + `</div>`
    + _SOUS.map(k => `<div class="sous-onglet-donnees" data-sous="${k}"${(k === _sousActif || (_sousActif === "recherche" && k === "tableau")) ? "" : " hidden"}>${_partiesSous[k]}</div>`).join("");   // « Recherche » affiche aussi les résultats (le tableau) juste dessous

  document.getElementById("zone-contenu").innerHTML = html;
  initialiserWidgets(); // branche les toggles (switch) et l'affichage live des barres
  // Barre de recherche : comme les boutons de la grille (bouton flottant ☰ → popup), les actions (Filtres, Ligne vide, Annuler,
  // Rétablir, exports…) tiennent dans UN popup ouvert par « ☰ » ; restent visibles : la saisie, le compteur et « Tout effacer ».
  { const barre = document.querySelector(".barre-recherche-lignes");
    if(barre && !barre.querySelector(".gy-menu-barre")){
      const gardes = new Set([document.getElementById("recherche-lignes"), document.getElementById("compteur-recherche"), document.getElementById("btn-effacer-criteres")]);
      const actions = [...barre.children].filter(e => !gardes.has(e));
      if(actions.length){
        const menu = document.createElement("div"); menu.className = "gy-menu-barre";
        const bouton = document.createElement("button");
        bouton.type = "button"; bouton.id = "btn-menu-barre"; bouton.textContent = "☰"; bouton.setAttribute("aria-expanded", "false");
        bouton.title = bouton.ariaLabel = tj("engine/static/engine/js/grille_tableur.js:tactile:fab");
        actions.forEach(e => menu.appendChild(e));
        barre.append(bouton, menu);
        const ouvrir = (o) => { barre.classList.toggle("gy-menu-barre-ouvert", o); bouton.setAttribute("aria-expanded", o ? "true" : "false"); };
        bouton.addEventListener("click", (ev) => { ev.stopPropagation(); ouvrir(!barre.classList.contains("gy-menu-barre-ouvert")); });
        menu.addEventListener("click", (ev) => { if(ev.target.closest("button, a")) ouvrir(false); });   // une action choisie referme le popup
        document.addEventListener("click", (ev) => { if(barre.isConnected && !ev.target.closest(".gy-menu-barre, #btn-menu-barre")) ouvrir(false); });
      }
    } }
  _initGrilleEditionFusion(table, slug, champs, data.lignes, _offsetPage, _vueReordonnee || !!table.groupe_id);
  if(_grilleCourante) _grilleCourante.total = (data.pagination || {}).total ?? data.lignes.length;

  const _activerSousOnglet = (nom) => {
    _sousOngletDonnees[slug] = nom;
    document.querySelectorAll("#onglets-donnees .onglet-vue").forEach(b => b.classList.toggle("actif", b.dataset.sous === nom));
    document.querySelectorAll("#zone-contenu .sous-onglet-donnees").forEach(d => { d.hidden = !(d.dataset.sous === nom || (nom === "recherche" && d.dataset.sous === "tableau")); });   // « Recherche » montre aussi les résultats
    if(nom === "tableau" || nom === "recherche") window.dispatchEvent(new Event("resize"));   // la grille se remesure une fois visible
    window.dispatchEvent(new Event("gy-sous-onglet"));   // la barre tactile reprend Annuler / Rétablir quand ceux de la barre d'outils sont masqués (autre sous-onglet)
  };
  document.querySelectorAll("#onglets-donnees .onglet-vue").forEach(b => b.addEventListener("click", () => _activerSousOnglet(b.dataset.sous)));
  // Les boutons de la barre qui agissent sur la grille (filtres, ligne vide, annuler, rétablir) ramènent au tableau.
  const _barreRecherche = document.querySelector(".barre-recherche-lignes");
  if(_barreRecherche) _barreRecherche.addEventListener("click", (ev) => {
    if(ev.target.closest("#btn-filtres-colonnes, #btn-ligne-vide, #btn-annuler-grille, #btn-refaire-grille")) _activerSousOnglet("tableau");
  }, true);
  // L'analyse exploratoire a maintenant son propre sous-onglet : ses cartes y sont dépliées d'emblée.
  { const _gs = document.getElementById("grille-stats-colonnes"), _ts = document.getElementById("toggle-stats-colonnes");
    if(_gs){ _gs.style.display = "grid"; if(_ts) _ts.classList.add("ouvert"); } }

  const toggleStats = document.getElementById("toggle-stats-colonnes");
  if(toggleStats){
    toggleStats.addEventListener("click", () => {
      const grille = document.getElementById("grille-stats-colonnes");
      const ouvert = grille.style.display !== "none";
      grille.style.display = ouvert ? "none" : "grid";
      toggleStats.classList.toggle("ouvert", !ouvert);
    });
  }

  const lignesParId = {};
  for(const ligne of data.lignes) lignesParId[ligne._id] = ligne;

  // Recherche dans TOUTE la table (côté serveur, toutes pages confondues) — l'ancienne recherche ne
  // filtrait que les lignes de la page affichée. Anti-rebond : une requête 400 ms après la frappe.
  const inputRecherche = document.getElementById("recherche-lignes");
  const _appliquer = () => afficherTable(slug, 1);
  if(inputRecherche){
    let minuterie = null;
    const lancer = () => {
      clearTimeout(minuterie);
      const q = inputRecherche.value.trim();
      if(q === _crit.q) return;
      _crit.q = q;
      _appliquer();
    };
    inputRecherche.addEventListener("input", () => { clearTimeout(minuterie); minuterie = setTimeout(lancer, 400); });
    inputRecherche.addEventListener("keydown", (ev) => { if(ev.key === "Enter"){ ev.preventDefault(); lancer(); } });
    if(_sousActif === "recherche" && _crit.q && document.activeElement === document.body){ inputRecherche.focus(); const _n = inputRecherche.value.length; try{ inputRecherche.setSelectionRange(_n, _n); }catch(e){} }
  }
  // Tri par clic sur l'en-tête : croissant -> décroissant -> ordre par défaut (plus récentes d'abord).
  // Ctrl/Cmd+clic sur n'importe quel en-tête (triable ou non) : sélectionne toute la colonne
  // dans la grille (pour fusion / changement de type), sans déclencher le tri.
  document.querySelectorAll("thead th[data-col-idx]").forEach(th => {
    th.addEventListener("click", (ev) => {
      if(ev.target.closest("button")) return;  // boutons ∑ / ↗ / partage de l'en-tête
      if(ev.ctrlKey || ev.metaKey || (window.gyTactile && window.gyTactile.prendre("colonne"))){
        ev.preventDefault();
        const c = parseInt(th.dataset.colIdx, 10);
        const grilleTbody = document.querySelector(".table-container table.donnees tbody");
        if(!grilleTbody) return;
        const indices = Array.from(grilleTbody.querySelectorAll("td[data-ligne-idx]")).map(td => parseInt(td.dataset.ligneIdx, 10));
        if(!indices.length) return;
        _selection = {table, slug, champs, r1: Math.min(...indices), r2: Math.max(...indices), c1: c, c2: c};
        _rafraichirSurbrillance(grilleTbody);
        if(_grilleCourante) _ouvrirBarreFusion(_grilleCourante.ligneParDispIndex);
        return;
      }
      if(!th.classList.contains("th-triable")) return;
      const s = th.dataset.triSlug;
      if(_crit.tri !== s){ _crit.tri = s; _crit.sens = "asc"; }
      else if(_crit.sens === "asc"){ _crit.sens = "desc"; }
      else { _crit.tri = ""; _crit.sens = ""; }
      _appliquer();
    });
  });
  document.querySelectorAll("input.filtre-colonne").forEach(inp => {
    const appliquerFiltre = () => {
      const v = inp.value.trim();
      if((_crit.filtres[inp.dataset.slug] || "") === v) return;
      if(v) _crit.filtres[inp.dataset.slug] = v; else delete _crit.filtres[inp.dataset.slug];
      _appliquer();
    };
    inp.addEventListener("keydown", (ev) => { if(ev.key === "Enter"){ ev.preventDefault(); appliquerFiltre(); } });
    inp.addEventListener("change", appliquerFiltre);
  });
  const btnFiltres = document.getElementById("btn-filtres-colonnes");
  if(btnFiltres) btnFiltres.onclick = () => {
    const ligneF = document.querySelector("tr.ligne-filtres");
    if(!ligneF) return;
    ligneF.hidden = !ligneF.hidden;
    _grilleFiltresOuverts[slug] = !ligneF.hidden;
    if(!ligneF.hidden){ const i = ligneF.querySelector("input"); if(i) i.focus(); }
  };
  const btnEffacer = document.getElementById("btn-effacer-criteres");
  if(btnEffacer) btnEffacer.onclick = () => { _grilleCriteres[slug] = {q:"", tri:"", sens:"", filtres:{}}; _appliquer(); };
  const _exporter = (format, bouton) => {
    const qs = _requeteCriteres(slug);
    gyTelecharger(`/api/tables/${table.id}/exporter-tableur/?format=${format}${qs ? "&" + qs : ""}`, `${table.slug}.${format}`,
      {bouton, succes: tj("engine/static/engine/js/index_1.js:1279:25")});
  };
  document.getElementById("btn-export-xlsx").onclick = (ev) => _exporter("xlsx", ev.currentTarget);
  document.getElementById("btn-export-parquet").onclick = (ev) => _exporter("parquet", ev.currentTarget);
  // Annuler / Rétablir : même pile que Ctrl+Z / Ctrl+Y (grille_tableur.js). L'état du bouton « Rétablir » et les libellés viennent du serveur
  // (l'historique des transformations survit au rechargement de la page).
  // Ligne vide : même création que le formulaire « Ajouter une ligne », sans valeur (constantes, formules et horodatages sont posés par le serveur).
  // Les champs obligatoires sont refusés par le serveur : le message s'affiche. Annulable par Ctrl+Z comme un collage.
  document.getElementById("btn-ligne-vide").onclick = async (ev) => {
    ev.currentTarget.disabled = true;
    try {
      const res = await api(`/api/tables/${table.id}/lignes/`, {method:"POST", body:"{}", headers:{"X-Idempotency-Key": tj("engine/static/engine/js/index_1.js:1289:122", {v1: Date.now(), v2: Math.random().toString(36).slice(2)})}});
      if(window.gyGrille && res && res.ligne && res.ligne._id) window.gyGrille.memoriser(table, slug, [{supprimerId: res.ligne._id}], tj("engine/static/engine/js/index_1.js:1290:136"));
      notifierSucces(tj("engine/static/engine/js/index_1.js:1291:23"));
      const pg = data.pagination || {};
      await afficherTable(slug, pg.taille ? Math.ceil(((pg.total || 0) + 1) / pg.taille) : undefined);   // page de la nouvelle ligne
      if(typeof rafraichirTables === "function") rafraichirTables();
    } catch(e) { /* déjà notifié par api() */ } finally { ev.currentTarget && (ev.currentTarget.disabled = false); }
  };
  document.getElementById("btn-annuler-grille").onclick = () => window.gyGrille && window.gyGrille.annuler();
  document.getElementById("btn-refaire-grille").onclick = () => window.gyGrille && window.gyGrille.refaire();
  if(window.gyGrille) window.gyGrille.majBoutons(table);
  const btnExportCsv = document.getElementById("btn-export-csv");   // absent sur une table NoSQL : un CSV à plat n'y a pas de sens, seul le JSON est proposé
  if(btnExportCsv) btnExportCsv.onclick = (ev) => _exporter("csv", ev.currentTarget);
  document.getElementById("btn-export-json").onclick = (ev) => _exporter("json", ev.currentTarget);

  const btnScannerQr = document.getElementById("btn-scanner-qr");
  if(btnScannerQr) btnScannerQr.onclick = async () => {
    let fichier;
    try { fichier = await ouvrirCaptureMedia("image"); } catch(e) { return; }
    if(!fichier) return;
    const corps = new FormData(); corps.append("image", fichier);
    try {
      const res = await fetch(`/api/tables/${table.id}/qrcode-rechercher/`, {method:"POST", body: corps});
      const data = await res.json();
      if(!res.ok) { notifierErreur(data.erreur || tj("engine/static/engine/js/index_1.js:1531:52")); return; }
      ouvrirModalLigne(table, data.ligne, data.ligne._id, slug);
    } catch(e) { notifierErreur(tj("engine/static/engine/js/index_1.js:1313:34")); }
  };

  for(const tr of document.querySelectorAll("tr[data-ligne-id]")){
    const ligneId = tr.dataset.ligneId;
    // Même formulaire que « Ajouter une ligne » (ouvrirModalLigne), prérempli ; l'enregistrement passe par api_modifier_ligne.
    tr.querySelector(".btn-modifier-ligne").onclick = () => {
      const ligneCourante = data.lignes.find(l => String(l._id) === String(ligneId));
      if(ligneCourante) ouvrirModalLigne(table, ligneCourante, ligneId, slug);
    };
    tr.querySelector(".btn-partager-ligne").onclick = () => _ouvrirModalPartage("ligne", table, ligneId);
    // Même mécanisme que l'export PDF de la table (fetch + blob + <a download>) : window.open
    // sur une réponse « attachment » ne télécharge rien dans la webview de l'app, et une erreur
    // serveur n'était jamais affichée.
    tr.querySelector(".btn-pdf-ligne").onclick = (ev) => gyTelecharger(
      `/api/tables/${table.id}/lignes/${ligneId}/recu-pdf/`, tj("engine/static/engine/js/index_1.js:1328:63", {v1: table.slug, v2: ligneId}),
      {bouton: ev.currentTarget, succes: tj("engine/static/engine/js/index_1.js:1329:43")});
    tr.querySelector(".btn-fiche-ligne").onclick = () => ouvrirFicheObjet(`enregistrement:${ligneId}`);
    tr.querySelector(".btn-couleur-ligne").onclick = () => {
      const l = data.lignes.find(x => String(x._id) === String(ligneId));
      _colorierLignes(table, slug, [Number(ligneId)], l && l._couleur);
    };
    tr.querySelector(".btn-supprimer-ligne").onclick = async () => {
      if(!await GY.confirmer(tj("engine/static/engine/js/index_1.js:1336:20"))) return;
      try {
        await api(`/api/tables/${table.id}/lignes/${ligneId}/supprimer/`, {method:"POST"});
        notifierSucces(tj("engine/static/engine/js/index_1.js:1339:25"));
        afficherTable(slug);
        rafraichirTables();
      } catch(e) { /* déjà notifié par api() */ }
    };
  }

  const _lienDerniere = document.getElementById("lien-derniere-groupe");
  if(_lienDerniere) _lienDerniere.onclick = (ev) => { ev.preventDefault(); tableActive = _derniereGroupe.slug; afficherTableauBord(_derniereGroupe.slug); rafraichirTables(); };
  // Même rendu que le formulaire client : image de garde, couleurs, polices… choisis dans l'éditeur visuel (formulaire_theme.js).
  { const _hote = document.querySelector(".corps-carte.gyf-hote"); if(_hote && window.gyFormTheme) gyFormTheme.appliquer(_hote, table.theme_formulaire || {}); }
  const _btnAjouterLigne = document.getElementById("btn-ajouter-ligne");
  // Formulaire conditionnel : mêmes sections / renvois que sur le portail client (formulaire_conditionnel_index.js).
  const _grilleAjout = document.querySelector(".corps-carte.gyf-hote .form-grille");
  if(window.gyFormCondIndex && gyFormCondIndex.actif(table) && _grilleAjout) { gyFormCondIndex.monter(table, _grilleAjout, _btnAjouterLigne); const _h = document.querySelector(".corps-carte.gyf-hote"); if(_h && window.gyFormTheme) gyFormTheme.appliquerDetails(_h, table.theme_formulaire || {}); }
  if(_btnAjouterLigne) _btnAjouterLigne.onclick = async () => {
    // Seuls les champs des sections traversées sont validés et envoyés (les autres restent vides ; le serveur le recalcule).
    const champs = (window.gyFormCondIndex && _grilleAjout && gyFormCondIndex.actif(table)) ? gyFormCondIndex.champsActifs(table, _grilleAjout) : table.champs;
    // Validation côté client : un champ obligatoire rempli uniquement d'espaces
    // (ou laissé vide) est refusé, quel que soit le type de champ.
    let toutesLesCasesValides = true;
    for(const c of champs){
      const el = document.getElementById("champ_"+c.slug);
      if(!el) continue;
      if(c.type === "formule" || c.type === "constante" || c.type === "horodatage" || c.type === "id_client") continue;
      el.classList.remove("champ-invalide");
      if(!c.obligatoire) continue;
      let vide;
      if(TYPES_BOOLEEN.includes(c.type)) vide = false; // case/toggle toujours "rempli"
      else if(TYPES_FICHIER.includes(c.type)) vide = !el.files || el.files.length === 0;
      else if(c.type === "choix_multiple") vide = el.selectedOptions.length === 0;
      else vide = (el.value || "").trim() === "";
      if(vide){
        el.classList.add("champ-invalide");
        toutesLesCasesValides = false;
      }
    }
    if(!toutesLesCasesValides){
      notifierErreur(tj("engine/static/engine/js/index_1.js:1370:23"));
      return;
    }

    const aDesFichiers = champs.some(c => TYPES_FICHIER.includes(c.type));
    try {
      if(aDesFichiers){
        const fd = new FormData();
        for(const c of champs){
          const el = document.getElementById("champ_"+c.slug);
          if(!el) continue;
      if(c.type === "formule" || c.type === "constante" || c.type === "horodatage" || c.type === "id_client") continue;
          if(TYPES_BOOLEEN.includes(c.type)) fd.append(c.slug, el.checked ? "1" : "0");
          else if(c.type === "choix_multiple") [...el.selectedOptions].forEach(o => fd.append(c.slug, o.value));
          else if(TYPES_FICHIER.includes(c.type)) { if(el.files[0]) fd.append(c.slug, el.files[0]); }
          else if(c.type === "relation"){
            const rel = _relationValeursPourCreation(c, document);
            for(const cle in rel) fd.append(cle, rel[cle]);
          }
          else fd.append(c.slug, el.value);
        }
        await api(`/api/tables/${table.id}/lignes/`, {method:"POST", body: fd});
      } else {
        const valeurs = {};
        for(const c of champs){
          const el = document.getElementById("champ_"+c.slug);
          if(!el) continue;
      if(c.type === "formule" || c.type === "constante" || c.type === "horodatage" || c.type === "id_client") continue;
          if(TYPES_BOOLEEN.includes(c.type)) valeurs[c.slug] = el.checked;
          else if(c.type === "choix_multiple") valeurs[c.slug] = [...el.selectedOptions].map(o => o.value);
          else if(c.type === "relation") Object.assign(valeurs, _relationValeursPourCreation(c, document));
          else valeurs[c.slug] = el.value;
        }
        await api(`/api/tables/${table.id}/lignes/`, {method:"POST", body: JSON.stringify(valeurs)});
      }
      notifierSucces(tj("engine/static/engine/js/index_1.js:1405:23"));
      afficherTable(slug);
      rafraichirTables();
    } catch(e) {
      // déjà notifié par api() (toast) ; on surligne aussi les champs fautifs
      // si le serveur les a identifiés (ex: type invalide).
      if(e && e.champsInvalides){
        for(const slug2 of Object.keys(e.champsInvalides)){
          const el = document.getElementById("champ_"+slug2);
          if(el) el.classList.add("champ-invalide");
        }
      }
      // EXCEPTION 2026-09-24 (voir obligation_pour_les_ia_avant_de_commencer.txt) : limite de
      // lignes atteinte — proposer d'étendre la table (nouvelle partie liée par GroupeTables)
      // et rejouer l'ajout dedans, plutôt que de laisser l'utilisateur bloqué.
      if(e && e.peutEtendre && await GY.confirmer(tj("engine/static/engine/js/index_1.js:1420:41"))){
        try{
          const ext = await api(`/api/tables/${table.id}/etendre-lignes/`, {method:"POST"});
          const nouvelleTable = ext.table;
          if(aDesFichiers){
            const fd2 = new FormData();
            for(const c of champs){
              const el = document.getElementById("champ_"+c.slug);
              if(!el || c.type === "formule" || c.type === "constante" || c.type === "horodatage" || c.type === "id_client") continue;
              if(TYPES_BOOLEEN.includes(c.type)) fd2.append(c.slug, el.checked ? "1" : "0");
              else if(c.type === "choix_multiple") [...el.selectedOptions].forEach(o => fd2.append(c.slug, o.value));
              else if(TYPES_FICHIER.includes(c.type)) { if(el.files[0]) fd2.append(c.slug, el.files[0]); }
              else fd2.append(c.slug, el.value);
            }
            await api(`/api/tables/${nouvelleTable.id}/lignes/`, {method:"POST", body: fd2});
          } else {
            const valeurs2 = {};
            for(const c of champs){
              const el = document.getElementById("champ_"+c.slug);
              if(!el || c.type === "formule" || c.type === "constante" || c.type === "horodatage" || c.type === "id_client") continue;
              if(TYPES_BOOLEEN.includes(c.type)) valeurs2[c.slug] = el.checked;
              else if(c.type === "choix_multiple") valeurs2[c.slug] = [...el.selectedOptions].map(o => o.value);
              else valeurs2[c.slug] = el.value;
            }
            await api(`/api/tables/${nouvelleTable.id}/lignes/`, {method:"POST", body: JSON.stringify(valeurs2)});
          }
          notifierSucces(tj("engine/static/engine/js/index_1.js:1446:27", {v1: nouvelleTable.nom}));
          rafraichirTables();
          afficherTable(nouvelleTable.slug);
        } catch(e2) { /* déjà notifié par api() */ }
      }
    }
  };
}
// ── Édition inline, recopie glissée (poignée) et fusion de cellules ────────
// (voir FusionCelluleTable côté backend). État minimal, pas de dépendance
// externe : une sélection en cours (_selection) et un drag de recopie
// (_recopie) par table affichée.
let _selection = null;   // {table, slug, champs, r1,c1,r2,c2}
let _rectsFusion = [];   // rectangles d'affichage des fusions de la page courante : [{r1,r2,c1,c2}] (r = index d'affichage)
let _glisseSel = null;   // glisser-sélectionner à la souris : {slug, r, c, bouge}
let _ignorerClicSel = false;  // le clic qui suit la fin d'un glisser ne doit pas refaire une sélection simple
let _pointage = null;         // pointage dans une formule (voir plus bas) : glisser en cours {r, c, rect, inp}
let _pointee = null;          // dernière référence insérée par pointage : {inp, debut, fin, ref}
let _pointageClic = false;    // le clic qui suit un pointage ne doit ni trier ni sélectionner

// Comme Excel : un rectangle qui touche une cellule fusionnée la couvre en entier (jamais de fusion à moitié prise).
// L'orientation (coin de départ r1/c1, coin mobile r2/c2) est conservée pour que Maj+flèches continue de s'étendre du bon côté.
function _normaliserSelectionFusions(){
  if(!_selection || !_rectsFusion.length) return;
  let rMin = Math.min(_selection.r1, _selection.r2), rMax = Math.max(_selection.r1, _selection.r2);
  let cMin = Math.min(_selection.c1, _selection.c2), cMax = Math.max(_selection.c1, _selection.c2);
  if(rMin === rMax && cMin === cMax) return;   // un simple clic sur une cellule fusionnée garde la barre « défusion »
  let change = true;
  while(change){
    change = false;
    for(const f of _rectsFusion){
      if(f.r1 <= rMax && rMin <= f.r2 && f.c1 <= cMax && cMin <= f.c2){
        if(f.r1 < rMin){ rMin = f.r1; change = true; }
        if(f.r2 > rMax){ rMax = f.r2; change = true; }
        if(f.c1 < cMin){ cMin = f.c1; change = true; }
        if(f.c2 > cMax){ cMax = f.c2; change = true; }
      }
    }
  }
  const r1Haut = _selection.r1 <= _selection.r2, c1Gauche = _selection.c1 <= _selection.c2;
  _selection.r1 = r1Haut ? rMin : rMax; _selection.r2 = r1Haut ? rMax : rMin;
  _selection.c1 = c1Gauche ? cMin : cMax; _selection.c2 = c1Gauche ? cMax : cMin;
}
let _recopie = null;     // {table, slug, champs, col, source, ligneId, cibles, ligneParDispIndex}
const _grilleFiltresOuverts = {};  // slug -> ligne de filtres affichée
let _grilleCourante = null;        // {table, slug, champs, ligneParDispIndex, offsetPage, nbLignes, vueReordonnee}

function _fermerBarreFusion(){
  const b = document.getElementById("barre-fusion");
  if(b) b.remove();
  _pointee = null; _marquerPointage(null);
}

function _initGrilleEditionFusion(table, slug, champs, lignes, offsetPage, vueReordonnee){
  // Conserve la cellule active d'un rendu à l'autre (tri, pagination, après une modification) comme Excel.
  const selPrecedente = (_selection && _selection.slug === slug) ? _selection : null;
  _selection = null; _fermerBarreFusion();
  const ligneParDispIndex = {};
  lignes.forEach((l, i) => { ligneParDispIndex[offsetPage + i] = l; });
  const grille = document.querySelector(".table-container table.donnees tbody");
  if(!grille) return;
  _grilleCourante = {table, slug, champs, ligneParDispIndex, offsetPage, nbLignes: lignes.length, vueReordonnee: !!vueReordonnee, grille};

  grille.querySelectorAll("td[data-ligne-idx]").forEach(td => {
    // Clic simple = début de sélection ; Maj+clic = étend le rectangle ;
    // Ctrl/Cmd+clic = sélectionne toute la ligne de cette cellule.
    td.addEventListener("click", (ev) => {
      if(ev.target.classList.contains("poignee-recopie")) return;
      if(ev.target.classList.contains("icone-type-cellule")) return;
      if(_ignorerClicSel){ _ignorerClicSel = false; return; }   // fin d'un glisser : la sélection est déjà faite
      const r = parseInt(td.dataset.ligneIdx, 10), c = parseInt(td.dataset.colIdx, 10);
      if((ev.ctrlKey || ev.metaKey || (window.gyTactile && window.gyTactile.prendre("ligne"))) && champs.length){
        _selection = {table, slug, champs, r1:r, r2:r, c1:0, c2:champs.length - 1};
      } else if((ev.shiftKey || (window.gyTactile && window.gyTactile.prendre("etendre"))) && _selection && _selection.slug === slug){
        _selection.r2 = r; _selection.c2 = c;
        _normaliserSelectionFusions();
      } else {
        _selection = {table, slug, champs, r1:r, r2:r, c1:c, c2:c};
      }
      _rafraichirSurbrillance(grille);
      const conteneur = td.closest(".grille-tableur");
      if(conteneur && !ev.target.closest("button, a, input, audio, video")) conteneur.focus({preventScroll:true});
      const _plage = _selection && (_selection.r1 !== _selection.r2 || _selection.c1 !== _selection.c2);
      if(td.dataset.fusionId && !_plage) _ouvrirBarreDefusion(table, slug, td.dataset.fusionId);
      else _ouvrirBarreFusion(ligneParDispIndex);
    });
    // Glisser la souris d'une cellule à une autre (même colonne, autre colonne, en diagonale) = sélection rectangulaire, comme Excel.
    td.addEventListener("mousedown", (ev) => {
      if(ev.button !== 0 || ev.shiftKey || ev.ctrlKey || ev.metaKey) return;
      if(ev.target.closest(".poignee-recopie, .icone-type-cellule, button, a, input, select, textarea, audio, video")) return;
      _glisseSel = {slug, r: parseInt(td.dataset.ligneIdx, 10), c: parseInt(td.dataset.colIdx, 10), bouge: false};
    });
    td.addEventListener("mouseenter", () => {
      if(!_glisseSel || _glisseSel.slug !== slug) return;
      const r = parseInt(td.dataset.ligneIdx, 10), c = parseInt(td.dataset.colIdx, 10);
      if(!_glisseSel.bouge && r === _glisseSel.r && c === _glisseSel.c) return;
      if(!_glisseSel.bouge){
        _glisseSel.bouge = true;
        const w = window.getSelection && window.getSelection(); if(w) w.removeAllRanges();   // pas de sélection de texte pendant le glisser
        const cont = td.closest(".grille-tableur"); if(cont) cont.classList.add("selection-glisse");
        if(cont) cont.focus({preventScroll:true});
      }
      _selection = {table, slug, champs, r1:_glisseSel.r, c1:_glisseSel.c, r2:r, c2:c};
      _normaliserSelectionFusions();
      _rafraichirSurbrillance(grille);
    });
    // Cellule fusionnée : double-clic = écrire dedans (texte, valeur ou =formule selon son type).
    if(td.dataset.fusionId){
      td.addEventListener("dblclick", () => _editerCelluleFusionnee(td, table, slug));
    }
    // Double-clic = édition inline directe de la cellule (façon Excel).
    if(td.classList.contains("cellule-editable")){
      td.addEventListener("dblclick", (ev) => {
        if(ev.target.classList.contains("poignee-recopie")) return;
        _demarrerEditionCellule(td, table, slug, champs, ligneParDispIndex);
      });
    }
    // Poignée de recopie : mousedown dessus démarre le glisser-copier
    // vers les cellules suivantes de la même colonne.
    const poignee = td.querySelector(".poignee-recopie");
    if(poignee){
      const debutRecopie = (ev) => {
        ev.preventDefault(); ev.stopPropagation();
        const r = parseInt(td.dataset.ligneIdx, 10), c = parseInt(td.dataset.colIdx, 10);
        const ligne = ligneParDispIndex[r];
        const champ = champs[c];
        _recopie = {table, slug, champs, col:c, source: ligne ? ligne[champ.slug] : "", ligneId: ligne ? ligne._id : null, cibles:new Set(), ligneParDispIndex};
      };
      poignee.addEventListener("mousedown", debutRecopie);
      poignee.addEventListener("touchstart", debutRecopie, {passive:false});   // doigt : même geste, la poignée est agrandie en CSS (pointer:coarse)
    }
  });
  document.querySelectorAll("td[data-ligne-idx]").forEach(td => {
    td.addEventListener("mouseenter", () => _cibleRecopie(td, slug));
  });
  function _cibleRecopie(td, slug){
      if(!_recopie || _recopie.slug !== slug) return;
      const c = parseInt(td.dataset.colIdx, 10);
      if(c !== _recopie.col) return;
      td.classList.add("cellule-recopie-cible");
      _recopie.cibles.add(td);
  }
  if(selPrecedente){
    const r = Math.min(Math.max(selPrecedente.r1, offsetPage), offsetPage + Math.max(lignes.length - 1, 0));
    _selection = {table, slug, champs, r1:r, r2:r, c1:selPrecedente.c1, c2:selPrecedente.c1};
    _rafraichirSurbrillance(grille);
  }
  if(window.gyGrille) window.gyGrille.brancher(_grilleCourante);
}

// Fin du glisser de la poignée de recopie : UN SEUL écouteur pour toute la page. Avant, chaque affichage
// de la grille en ajoutait un nouveau (jamais retiré) : après N rafraîchissements, N écouteurs, dont
// N-1 avec une correspondance ligne/rang périmée — la recopie écrivait alors plusieurs fois, parfois
// dans de mauvaises lignes. La correspondance voyage désormais avec _recopie.
async function _finRecopie(){
  if(!_recopie) return;
  const recopie = _recopie; _recopie = null;
  const champ = recopie.champs[recopie.col];
  const annulation = [];
  for(const td of recopie.cibles){
    const r = parseInt(td.dataset.ligneIdx, 10);
    const ligne = recopie.ligneParDispIndex[r];
    td.classList.remove("cellule-recopie-cible");
    if(!ligne || ligne._id === recopie.ligneId) continue;
    try {
      await api(`/api/tables/${recopie.table.id}/lignes/${ligne._id}/modifier/`, {
        method:"POST", body: JSON.stringify({[champ.slug]: recopie.source}),
      });
      annulation.push({ligneId: ligne._id, valeurs: {[champ.slug]: ligne[champ.slug] ?? ""}});
    } catch(e) { /* déjà notifié par api() */ }
  }
  if(annulation.length && window.gyGrille) window.gyGrille.memoriser(recopie.table, recopie.slug, annulation, tj("engine/static/engine/js/index_1.js:lbl_recopie"));
  if(recopie.slug === tableActive && recopie.cibles.size) afficherTable(recopie.slug);
}
document.addEventListener("mouseup", _finRecopie);
// Fin du glisser-sélectionner : un seul écouteur pour toute la page (voir _glisseSel). La barre de fusion s'ouvre sur le rectangle.
document.addEventListener("mouseup", () => {
  const g = _glisseSel; _glisseSel = null;
  document.querySelectorAll(".grille-tableur.selection-glisse").forEach(el => el.classList.remove("selection-glisse"));
  if(!g || !g.bouge || !_selection || !_grilleCourante) return;
  _ignorerClicSel = true; setTimeout(() => { _ignorerClicSel = false; }, 0);
  _ouvrirBarreFusion(_grilleCourante.ligneParDispIndex);
});
document.addEventListener("touchend", _finRecopie);
document.addEventListener("touchmove", (ev) => {   // le doigt glisse : la cellule sous le doigt devient une cible (pas de mouseenter au toucher)
  if(!_recopie) return;
  const t = ev.touches[0], el = t && document.elementFromPoint(t.clientX, t.clientY), td = el && el.closest("td[data-ligne-idx]");
  if(td){ ev.preventDefault(); _cibleRecopie(td, _recopie.slug); }
}, {passive:false});

// ── Couleur d'une LIGNE et de CELLULES (grille « Données », reprise dans le PDF de la table) ────────────────────────────────
// Trois couleurs indépendantes, la plus précise gagne : cellule > ligne > colonne. Colorier une ligne ne touche pas les couleurs de
// ses cellules, et inversement. API (engine/views_couleur.py) : POST /api/tables/<id>/lignes-couleur/ {couleur, lignes:[id]}
// et POST /api/tables/<id>/cellules-couleur/ {couleur, lignes:[id], champs:[slug]} ("" = retirer). Même fenêtre de choix que les colonnes.
const _ALPHA_MARQUE = "59";   // ≈ 35 % : même teinte que couleur_colonne.PART_TEINTE_MARQUE (PDF)
function _hexMarque(h){ return (typeof h === "string" && /^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(h)) ? h : ""; }
// Fond écran d'une couleur « #rrggbb » (teinte standard : opacité par défaut) ou « #rrggbbaa » (opacité choisie, gardée telle quelle).
function _fondCouleur(h, alphaDefaut){ return h.length === 9 ? h : h + alphaDefaut; }
function _pageGrille(slug){ return (_paginationEtat[slug] || {}).page || 1; }
async function _colorierLignes(table, slug, ids, valeur){
  if(!ids.length) return;
  const couleur = await GYCouleur.choisir({titre: ids.length > 1 ? tj("engine/static/engine/js/index_1.js:1643:69", {v1: ids.length}) : tj("engine/static/engine/js/index_1.js:1643:105"), valeur: valeur || "", apercu: tj("engine/static/engine/js/index_1.js:1643:158")});
  if(couleur === null) return;
  try {
    await api(`/api/tables/${table.id}/lignes-couleur/`, {method: "POST", body: JSON.stringify({couleur, lignes: ids})});
    notifierSucces(couleur ? tj("engine/static/engine/js/index_1.js:1647:31", {v1: ids.length}) : tj("engine/static/engine/js/index_1.js:1647:70", {v1: ids.length}));
    afficherTable(slug, _pageGrille(slug));
  } catch(e) { /* déjà notifié par api() */ }
}
async function _colorierSelection(ligneParDispIndex){
  if(!_selection) return;
  const { table, champs, slug } = _selection;
  const rMin = Math.min(_selection.r1, _selection.r2), rMax = Math.max(_selection.r1, _selection.r2);
  const cMin = Math.min(_selection.c1, _selection.c2), cMax = Math.max(_selection.c1, _selection.c2);
  const ids = [];
  for(let r = rMin; r <= rMax; r++){ const l = ligneParDispIndex[r]; if(l) ids.push(l._id); }
  const slugs = champs.slice(cMin, cMax + 1).map(c => c.slug);
  if(!ids.length || !slugs.length) return;
  const l0 = ligneParDispIndex[rMin];
  const nb = ids.length * slugs.length;
  const couleur = await GYCouleur.choisir({
    titre: nb > 1 ? tj("engine/static/engine/js/index_1.js:1663:22", {v1: nb}) : tj("engine/static/engine/js/index_1.js:1663:52"),
    valeur: (l0 && l0._couleurs && l0._couleurs[slugs[0]]) || "", apercu: tj("engine/static/engine/js/index_1.js:1664:76")});
  if(couleur === null) return;
  try {
    await api(`/api/tables/${table.id}/cellules-couleur/`, {method: "POST", body: JSON.stringify({couleur, lignes: ids, champs: slugs})});
    notifierSucces(couleur ? tj("engine/static/engine/js/index_1.js:1668:31", {v1: nb}) : tj("engine/static/engine/js/index_1.js:1668:64", {v1: nb}));
    afficherTable(slug, _pageGrille(slug));
  } catch(e) { /* déjà notifié par api() */ }
}

function _rafraichirSurbrillance(grille){
  grille.querySelectorAll(".cellule-selectionnee").forEach(td => td.classList.remove("cellule-selectionnee"));
  grille.querySelectorAll(".icone-type-cellule").forEach(el => el.remove());
  if(!_selection) return;
  const rMin = Math.min(_selection.r1, _selection.r2), rMax = Math.max(_selection.r1, _selection.r2);
  const cMin = Math.min(_selection.c1, _selection.c2), cMax = Math.max(_selection.c1, _selection.c2);
  let tdUnique = null;
  grille.querySelectorAll("td[data-ligne-idx]").forEach(td => {
    const r = parseInt(td.dataset.ligneIdx, 10), c = parseInt(td.dataset.colIdx, 10);
    if(r >= rMin && r <= rMax && c >= cMin && c <= cMax){
      td.classList.add("cellule-selectionnee");
      if(r === rMin && r === rMax && c === cMin && c === cMax) tdUnique = td;
    }
  });
  // Une seule cellule sélectionnée (ni déjà fusionnée — elle a sa propre
  // barre "Défusionner") : icône directement sur la cellule pour changer
  // son type, sans passer par une fusion (voir _ouvrirBarreFusion,
  // forcerUneCellule). La barre de fusion, elle, s'ouvre automatiquement
  // dès que 2 cellules ou plus sont sélectionnées (voir le gestionnaire de clic).
  if(tdUnique && !tdUnique.dataset.fusionId){
    tdUnique.style.position = tdUnique.style.position || "relative";
    const icone = document.createElement("button");
    icone.type = "button";
    icone.className = "icone-type-cellule";
    icone.title = tj("engine/static/engine/js/index_1.js:1697:20");
    icone.textContent = "🔧";
    icone.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if(_grilleCourante && _grilleCourante.vueReordonnee){
        notifierErreur(tj("engine/static/engine/js/index_1.js:1702:25"));
        return;
      }
      if(_grilleCourante) _ouvrirBarreFusion(_grilleCourante.ligneParDispIndex, /*forcerUneCellule=*/true);
    });
    tdUnique.appendChild(icone);
  }
  // Une seule cellule (fusionnée ou non) -> pas de fusion ici, mais sa couleur propre se choisit : 🎨 à gauche du 🔧.
  if(tdUnique && !tdUnique.dataset.fusionId && _grilleCourante){
    const iconeCouleur = document.createElement("button");
    iconeCouleur.type = "button";
    iconeCouleur.className = "icone-type-cellule icone-couleur-cellule";
    iconeCouleur.title = tj("engine/static/engine/js/index_1.js:1714:27");
    iconeCouleur.textContent = "🎨";
    iconeCouleur.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if(_selection) _colorierSelection(_grilleCourante.ligneParDispIndex);
    });
    tdUnique.appendChild(iconeCouleur);
  }
}

function _ouvrirBarreFusion(ligneParDispIndex, forcerUneCellule){
  _fermerBarreFusion();
  if(!_selection) return;
  const { table, champs } = _selection;
  const rMin = Math.min(_selection.r1, _selection.r2), rMax = Math.max(_selection.r1, _selection.r2);
  const cMin = Math.min(_selection.c1, _selection.c2), cMax = Math.max(_selection.c1, _selection.c2);
  const uneSeuleCellule = (rMin === rMax && cMin === cMax);
  // Une seule cellule cliquée normalement : pas de barre automatique (elle
  // s'ouvrait à chaque clic et masquait le bas de la grille). Ouverture
  // volontaire (bouton "🔗 Fusion / Type cellule", forcerUneCellule) : la
  // barre s'ouvre quand même — c'est justement le mécanisme qui sert à
  // changer le type d'UNE cellule sans en fusionner plusieurs (voir
  // FusionCelluleTable côté backend, ligne_debut==ligne_fin et col_debut==col_fin).
  if(uneSeuleCellule && !forcerUneCellule){
    // Navigation clavier sur une cellule fusionnée : même barre que le clic (type, contenu, défusion).
    const tdFus = document.querySelector(`.grille-tableur td[data-ligne-idx="${rMin}"][data-col-idx="${cMin}"]`);
    if(tdFus && tdFus.dataset.fusionId) _ouvrirBarreDefusion(table, _selection.slug, tdFus.dataset.fusionId);
    return;
  }
  const vueReordonnee = !!(_grilleCourante && _grilleCourante.vueReordonnee);
  const idsLignes = [];
  for(let r = rMin; r <= rMax; r++){ const l = ligneParDispIndex[r]; if(l) idsLignes.push(l._id); }
  const totalLignes = (_paginationEtat[_selection.slug] || {}).total || Object.keys(ligneParDispIndex).length;
  const chronoDebut = totalLignes - 1 - rMax, chronoFin = totalLignes - 1 - rMin;

  const options = FusionCelluleTable_TYPE_CHOICES.map(([v,l]) => `<option value="${v}">${escapeHtml(l)}</option>`).join("");
  const barre = document.createElement("div");
  barre.id = "barre-fusion";
  barre.style = "position:sticky;bottom:0;z-index:5;display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:8px;background:var(--bg2,#111);border:1px solid var(--border);border-radius:6px;margin-top:8px";
  barre.innerHTML = `
    <span style="font-size:12px;color:var(--muted)" title="${tj("engine/static/engine/js/index_1.js:1754:61")}">${uneSeuleCellule ? tj("engine/static/engine/js/index_1.js:1754:149", {v1: _refCellule(_selection.slug, rMin, cMin)}) : tj("engine/static/engine/js/index_1.js:1754:205", {v1: _refCellule(_selection.slug, rMax, cMin), v2: _refCellule(_selection.slug, rMin, cMax), v3: rMax-rMin+1, v4: cMax-cMin+1})}</span>
    <span class="agregats-selection" id="agregats-selection"></span>
    ${vueReordonnee ? tj("engine/static/engine/js/index_1.js:1756:24") : `
    <select id="fusion-nature" style="padding:4px;font-size:12px">${options}</select>
    <input id="fusion-valeur" type="text" placeholder="${tj("engine/static/engine/js/index_1.js:1758:57")}" style="padding:4px;font-size:12px;flex:1;min-width:120px">
    <button id="fusion-assistant" type="button" title="${tj("engine/static/engine/js/index_1.js:a:fusion_assistant:titre")}" style="padding:4px 10px;font-size:12px">${tj("engine/static/engine/js/index_1.js:a:fusion_assistant:bouton")}</button>
    <span id="fusion-pointage-aide" hidden style="flex-basis:100%;font-size:11px;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:a:fusion_pointage:aide")}</span>
    <button id="fusion-valider" style="padding:4px 10px;font-size:12px">${uneSeuleCellule ? tj("engine/static/engine/js/index_1.js:1759:94") : "🔗 Fusionner"}</button>`}
    <button id="selection-couleur-cellules" title="${tj("engine/static/engine/js/index_1.js:1760:53")}" style="padding:4px 10px;font-size:12px">${tj("engine/static/engine/js/index_1.js:1760:159")}</button>
    <button id="selection-couleur-lignes" title="${tj("engine/static/engine/js/index_1.js:1761:51")}" style="padding:4px 10px;font-size:12px">🎨 ${idsLignes.length} ${tj("engine/static/engine/js/index_1.js:1761:195")}</button>
    <button id="selection-supprimer-lignes" class="danger" style="padding:4px 10px;font-size:12px">${tj("engine/static/engine/js/index_1.js:1762:101")} ${idsLignes.length} ${tj("engine/static/engine/js/index_1.js:1762:133")}</button>
    <button id="fusion-annuler" style="padding:4px 10px;font-size:12px">${tj("engine/static/engine/js/index_1.js:1763:74")}</button>
  `;
  document.querySelector(".table-container.grille-tableur").after(barre);
  if(uneSeuleCellule && !vueReordonnee){
    // Changer la nature d'UNE cellule : on part de sa nature et de sa valeur actuelles (sinon elle s'afficherait vide).
    const _ch = champs[cMin], _l = ligneParDispIndex[rMin], _sel = document.getElementById("fusion-nature");
    const _t = _ch && (_ch.type_champ || _ch.type);
    if(_sel && _t && [..._sel.options].some(o => o.value === _t)) _sel.value = _t;
    const _v = _ch && _l ? _l[_ch.slug] : null;
    if(_v !== null && _v !== undefined && typeof _v !== "object") document.getElementById("fusion-valeur").value = String(_v);
  }
  if(window.gyGrille) window.gyGrille.afficherAgregats();
  document.getElementById("selection-couleur-cellules").onclick = () => _colorierSelection(ligneParDispIndex);
  document.getElementById("selection-couleur-lignes").onclick = () => {
    const l0 = ligneParDispIndex[rMin];
    _colorierLignes(table, _selection.slug, idsLignes, l0 && l0._couleur);
  };
  document.getElementById("selection-supprimer-lignes").onclick = async () => {
    if(!idsLignes.length || !_selection) return;
    if(!await GY.confirmer(tj("engine/static/engine/js/index_1.js:1782:18", {v1: idsLignes.length}))) return;
    const slugCourant = _selection.slug;
    try {
      const res = await api(`/api/tables/${table.id}/lignes/supprimer-lot/`, {method:"POST", body: JSON.stringify({ids: idsLignes})});
      notifierSucces(tj("engine/static/engine/js/index_1.js:1786:23", {v1: res.supprimees, v2: res.ignorees ? tj("engine/static/engine/js/index_1.js:1786:80", {v1: res.ignorees}) : ""}));
      _selection = null; _fermerBarreFusion();
      afficherTable(slugCourant); rafraichirTables();
    } catch(e) { /* déjà notifié par api() */ }
  };

  document.getElementById("fusion-annuler").onclick = () => { _selection = null; _fermerBarreFusion(); _rafraichirSurbrillance(document.querySelector(".grille-tableur tbody")); };
  if(vueReordonnee) return;
  document.getElementById("fusion-assistant").onclick = () => _assistantFormuleFusion(table);
  _brancherAidePointage();
  document.getElementById("fusion-valider").onclick = async () => {
    if(!_selection) return;   // la grille a été redessinée entre-temps : plus de sélection à fusionner
    const slugCourant = _selection.slug;
    try {
      const res = await api(`/api/tables/${table.id}/fusions/`, {method:"POST", body: JSON.stringify({
        ligne_debut: chronoDebut, ligne_fin: chronoFin, col_debut: cMin, col_fin: cMax,
        type_affiche: (document.getElementById("fusion-valeur").value.trim().startsWith("=") ? "formule" : document.getElementById("fusion-nature").value),
        valeur_affichee: document.getElementById("fusion-valeur").value,
      })});
      // Dénaturation automatique (voir TableDynamique.recalculer_denaturee) : mise à jour du badge.
      _majBadgeDenaturee(table, res);
      notifierSucces(uneSeuleCellule ? tj("engine/static/engine/js/index_1.js:1804:41") : tj("engine/static/engine/js/index_1.js:1804:73"));
      _selection = null; _fermerBarreFusion();
      afficherTable(slugCourant);
    } catch(e) { /* déjà notifié par api() */ }
  };
}

// Choix de nature repris de FusionCelluleTable.TYPE_CHOICES (models.py) —
// dupliqué ici car c'est un simple <select> statique côté frontend.
const FusionCelluleTable_TYPE_CHOICES = [
  ["texte",tj("engine/static/engine/js/index_1.js:1814:13")], ["texte_long",tj("engine/static/engine/js/index_1.js:1814:37")], ["nombre",tj("engine/static/engine/js/index_1.js:1814:62")], ["decimal",tj("engine/static/engine/js/index_1.js:type_decimal")],
  ["date",tj("engine/static/engine/js/index_1.js:1815:12")], ["heure",tj("engine/static/engine/js/index_1.js:1815:30")], ["datetime",tj("engine/static/engine/js/index_1.js:1815:52")], ["horodatage",tj("engine/static/engine/js/index_1.js:1815:84")], ["duree",tj("engine/static/engine/js/index_1.js:type_duree")],
  ["booleen",tj("engine/static/engine/js/index_1.js:type_booleen")], ["monnaie",tj("engine/static/engine/js/index_1.js:type_monnaie")], ["pourcentage",tj("engine/static/engine/js/index_1.js:type_pourcentage")], ["note",tj("engine/static/engine/js/index_1.js:type_note")],
  ["email",tj("engine/static/engine/js/index_1.js:type_email")], ["url",tj("engine/static/engine/js/index_1.js:type_url")], ["telephone",tj("engine/static/engine/js/index_1.js:type_telephone")], ["couleur",tj("engine/static/engine/js/index_1.js:type_couleur")], ["json",tj("engine/static/engine/js/index_1.js:type_json")],
  ["formule",tj("engine/static/engine/js/index_1.js:type_formule")],
];

// Référence style tableur d'une cellule, utilisable dans les formules : colonne = lettre (A, B…),
// ligne = rang de CRÉATION (1 = la plus ancienne), stable quand on ajoute des lignes.
function _lettreColonne(i){ let s = "", n = i + 1; while(n){ const r = (n - 1) % 26; s = String.fromCharCode(65 + r) + s; n = Math.floor((n - 1) / 26); } return s; }
function _refCellule(slug, dispIdx, col){
  const total = (_paginationEtat[slug] || {}).total || 0;
  return _lettreColonne(col) + (total - dispIdx);
}

// Le statut « dénaturée » est dérivé côté serveur (fusion réelle ou type de cellule modifié) : on répercute
// la réponse sur le badge de l'en-tête sans attendre le rechargement.
function _majBadgeDenaturee(table, res){
  if(!res || !("table_denaturee" in res)) return;
  table.denaturee = res.table_denaturee;
  const badge = document.getElementById("badge-table-denaturee");
  if(badge) badge.style.display = res.table_denaturee ? "inline" : "none";
}

// ── Pointage à la souris dans une formule de cellule fusionnée (comme Excel) ─────────────────────────────────────────────────────
// Tant que le champ « =… » (barre de fusion ou saisie sur place d'une fusion) est actif et que le curseur suit « = », « ( », « ; » ou un
// opérateur, un clic ou un glisser sur la grille INSÈRE la référence (B2, B2:B5) au curseur et un clic sur un en-tête insère la colonne
// entière ([Nom]) : la sélection (= la fusion) n'est pas touchée, la barre n'est pas reconstruite, la saisie n'est jamais perdue.
// Un nouveau clic remplace la référence qui vient d'être pointée tant que rien n'a été tapé. UN SEUL jeu d'écouteurs, posé au chargement.
function _champFormule(){
  const a = document.activeElement;
  const ok = a && a.tagName === "INPUT" && (a.id === "fusion-valeur" || (a.classList.contains("edition-cellule") && a.closest("td[data-fusion-id]")));
  return ok && _grilleCourante && !_grilleCourante.vueReordonnee && a.value.trim().startsWith("=") ? a : null;
}
function _zoneInsertion(inp){
  const v = inp.value, a = inp.selectionStart == null ? v.length : inp.selectionStart, b = inp.selectionEnd == null ? a : inp.selectionEnd;
  if(_pointee && _pointee.inp === inp && a === b && a === _pointee.fin && v.slice(_pointee.debut, _pointee.fin) === _pointee.ref) return {debut: _pointee.debut, fin: _pointee.fin};
  return /[=(;+\-*\/]\s*$/.test(v.slice(0, a)) ? {debut: a, fin: b} : null;
}
function _rectPointage(p, r, c){ return {rMin: Math.min(p.r, r), rMax: Math.max(p.r, r), cMin: Math.min(p.c, c), cMax: Math.max(p.c, c)}; }
function _rectsSeTouchent(a, b){ return a.rMin <= b.rMax && b.rMin <= a.rMax && a.cMin <= b.cMax && b.cMin <= a.cMax; }
// Zone de la cellule en cours d'écriture (sélection élargie aux fusions qu'elle touche) : s'y référer serait un cycle.
function _rectPropre(){
  const s = _selection; if(!s) return null;
  const z = {rMin: Math.min(s.r1, s.r2), rMax: Math.max(s.r1, s.r2), cMin: Math.min(s.c1, s.c2), cMax: Math.max(s.c1, s.c2)};
  _rectsFusion.forEach(f => { const o = {rMin: f.r1, rMax: f.r2, cMin: f.c1, cMax: f.c2};
    if(_rectsSeTouchent(z, o)){ z.rMin = Math.min(z.rMin, o.rMin); z.rMax = Math.max(z.rMax, o.rMax); z.cMin = Math.min(z.cMin, o.cMin); z.cMax = Math.max(z.cMax, o.cMax); } });
  return z;
}
function _marquerPointage(rect){
  document.querySelectorAll("td[data-pointee]").forEach(td => { td.style.outline = ""; td.removeAttribute("data-pointee"); });
  if(!rect) return;
  document.querySelectorAll(".grille-tableur td[data-ligne-idx]").forEach(td => {
    const r = parseInt(td.dataset.ligneIdx, 10), c = parseInt(td.dataset.colIdx, 10);
    if(r >= rect.rMin && r <= rect.rMax && c >= rect.cMin && c <= rect.cMax){ td.dataset.pointee = "1"; td.style.outline = "2px dashed var(--accent, #4da3ff)"; }
  });
}
function _insererReference(inp, ref){
  const z = _zoneInsertion(inp); if(!z) return;
  inp.value = inp.value.slice(0, z.debut) + ref + inp.value.slice(z.fin);
  const p = z.debut + ref.length; inp.setSelectionRange(p, p); inp.focus();
  _pointee = {inp, debut: z.debut, fin: p, ref};
  const n = document.getElementById("fusion-nature"); if(n && inp.id === "fusion-valeur") n.value = "formule";
  inp.dispatchEvent(new Event("input", {bubbles: true}));
}
function _majAidePointage(){
  const a = document.getElementById("fusion-pointage-aide"), i = document.getElementById("fusion-valeur");
  if(a && i) a.hidden = !i.value.trim().startsWith("=");
}
function _brancherAidePointage(){
  const i = document.getElementById("fusion-valeur");
  if(i) i.addEventListener("input", _majAidePointage);
  _majAidePointage();
}
document.addEventListener("mousedown", (ev) => {
  if(ev.button !== 0 || !ev.target.closest) return;
  const inp = _champFormule();
  if(!inp || !_zoneInsertion(inp)) return;
  const td = ev.target.closest(".grille-tableur td[data-ligne-idx]");
  const th = td ? null : ev.target.closest(".grille-tableur th[data-col-idx]");
  if(!td && !(th && !ev.target.closest("button, a, input, select"))) return;
  ev.preventDefault(); ev.stopPropagation();   // le champ garde le focus (sinon la saisie en cours serait validée) ; la grille ne sélectionne rien
  _pointage = td ? {r: parseInt(td.dataset.ligneIdx, 10), c: parseInt(td.dataset.colIdx, 10), rect: null, inp}
                 : {r: null, c: parseInt(th.dataset.colIdx, 10), rect: null, inp};
}, true);
document.addEventListener("mouseover", (ev) => {
  if(!_pointage || _pointage.r === null || !ev.target.closest) return;
  const td = ev.target.closest(".grille-tableur td[data-ligne-idx]"); if(!td) return;
  _pointage.rect = _rectPointage(_pointage, parseInt(td.dataset.ligneIdx, 10), parseInt(td.dataset.colIdx, 10));
  _marquerPointage(_pointage.rect);
}, true);
document.addEventListener("mouseup", () => {
  const p = _pointage; _pointage = null;
  if(!p) return;
  _pointageClic = true; setTimeout(() => { _pointageClic = false; }, 0);
  const g = _grilleCourante;
  if(!g || !document.body.contains(p.inp)) return;
  if(p.r === null){   // en-tête : colonne entière (son nom, ou son identifiant si le nom contient « ] »)
    const ch = g.champs[p.c]; if(!ch) return;
    _marquerPointage(null);
    _insererReference(p.inp, `[${ch.nom && !ch.nom.includes("]") ? ch.nom : ch.slug}]`);
    return;
  }
  const rect = p.rect || _rectPointage(p, p.r, p.c), propre = _rectPropre();
  if(propre && _rectsSeTouchent(rect, propre)){ _marquerPointage(null); notifierErreur(tj("engine/static/engine/js/index_1.js:a:fusion_pointage:soi_meme")); return; }
  const total = (_paginationEtat[g.slug] || {}).total || 0;
  const a = _lettreColonne(rect.cMin) + (total - rect.rMax), b = _lettreColonne(rect.cMax) + (total - rect.rMin);   // rang de création : 1 = la plus ancienne
  _marquerPointage(rect);
  _insererReference(p.inp, a === b ? a : `${a}:${b}`);
}, true);
document.addEventListener("click", (ev) => { if(_pointageClic){ _pointageClic = false; ev.stopPropagation(); ev.preventDefault(); } }, true);

// Enregistre le type et/ou le contenu d'une cellule fusionnée (POST sur la fusion ; le serveur valide
// le contenu selon le type et évalue les formules). Une saisie qui commence par « = » devient une formule.
async function _enregistrerFusion(table, slug, fusionId, type, valeur){
  if(String(valeur).trim().startsWith("=")) type = "formule";
  const res = await api(`/api/tables/${table.id}/fusions/${fusionId}/`, {method:"POST", body: JSON.stringify({type_affiche: type, valeur_affichee: valeur})});
  _majBadgeDenaturee(table, res);
  afficherTable(slug);
}

// Écrire directement dans une cellule fusionnée (double-clic, Entrée ou F2) : champ de saisie sur place.
function _editerCelluleFusionnee(td, table, slug, saisie){
  if(!td || !td.dataset.fusionId || td.querySelector("input.edition-cellule")) return;
  if(_grilleCourante && _grilleCourante.vueReordonnee) return;
  const contenuOriginal = td.innerHTML;
  const type = td.dataset.fusionType || "texte";
  const input = document.createElement("input");
  input.type = "text"; input.className = "edition-cellule";
  input.value = td.dataset.fusionValeur || "";
  input.placeholder = type === "formule" ? "=SOMME([Colonne])" : tj("engine/static/engine/js/index_1.js:lbl_contenu");
  input.style = "width:100%;padding:3px;font-size:13px";
  if(saisie !== undefined) input.value = saisie;  // frappe directe : remplace le contenu, comme Excel
  td.innerHTML = ""; td.appendChild(input); input.focus();
  if(saisie === undefined) input.select();
  let termine = false;
  const valider = async () => {
    if(termine) return; termine = true;
    if(input.value === (td.dataset.fusionValeur || "")){ td.innerHTML = contenuOriginal; if(window.gyGrille) window.gyGrille.refocaliser(); return; }
    try { await _enregistrerFusion(table, slug, td.dataset.fusionId, type, input.value); }
    catch(e) { td.innerHTML = contenuOriginal; /* erreur déjà notifiée par api() */ }
  };
  input.addEventListener("keydown", (ev) => {
    ev.stopPropagation();
    if(ev.key === "Enter"){ ev.preventDefault(); input.blur(); }
    else if(ev.key === "Escape"){
      ev.preventDefault(); termine = true;
      input.removeEventListener("blur", valider);
      td.innerHTML = contenuOriginal;
      if(window.gyGrille) window.gyGrille.refocaliser();
    }
  });
  input.addEventListener("blur", valider);
}

// Barre d'une cellule fusionnée : type de donnée attendu, contenu (texte, valeur ou =formule), défusion.
function _ouvrirBarreDefusion(table, slug, fusionId){
  _fermerBarreFusion();
  const td = document.querySelector(`.grille-tableur td[data-fusion-id="${fusionId}"]`);
  const typeActuel = (td && td.dataset.fusionType) || "texte";
  const valeurActuelle = (td && td.dataset.fusionValeur) || "";
  const vueReordonnee = !!(_grilleCourante && _grilleCourante.vueReordonnee);
  const options = FusionCelluleTable_TYPE_CHOICES.map(([v,l]) => `<option value="${v}"${v === typeActuel ? " selected" : ""}>${escapeHtml(l)}</option>`).join("");
  const barre = document.createElement("div");
  barre.id = "barre-fusion";
  barre.style = "position:sticky;bottom:0;z-index:5;display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:8px;background:var(--bg2,#111);border:1px solid var(--border);border-radius:6px;margin-top:8px";
  barre.innerHTML = `<span style="font-size:12px;color:var(--muted)" title="${tj("engine/static/engine/js/index_1.js:1892:78")}">${tj("engine/static/engine/js/index_1.js:1892:139")} ${td ? _refCellule(slug, parseInt(td.dataset.ligneIdx, 10), parseInt(td.dataset.colIdx, 10)) : ""}</span>
    <select id="fusion-nature" title="${tj("engine/static/engine/js/index_1.js:1893:40")}" style="padding:4px;font-size:12px">${options}</select>
    <input id="fusion-valeur" type="text" placeholder="${tj("engine/static/engine/js/index_1.js:1894:57")}" title="${tj("engine/static/engine/js/index_1.js:a:fusion_pointage:exemples")}" style="padding:4px;font-size:12px;flex:1;min-width:140px">
    <button id="fusion-assistant" type="button" title="${tj("engine/static/engine/js/index_1.js:a:fusion_assistant:titre")}" style="padding:4px 10px;font-size:12px">${tj("engine/static/engine/js/index_1.js:a:fusion_assistant:bouton")}</button>
    <span id="fusion-pointage-aide" hidden style="flex-basis:100%;font-size:11px;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:a:fusion_pointage:aide")}</span>
    <button id="fusion-enregistrer" class="principal" style="padding:4px 10px;font-size:12px">${tj("engine/static/engine/js/index_1.js:1895:96")}</button>
    <button id="fusion-defusionner" class="danger" style="padding:4px 10px;font-size:12px">${tj("engine/static/engine/js/index_1.js:1896:93")}</button>
    <button id="fusion-annuler" style="padding:4px 10px;font-size:12px">${tj("engine/static/engine/js/index_1.js:1897:74")}</button>`;
  document.querySelector(".table-container.grille-tableur").after(barre);
  const champValeur = document.getElementById("fusion-valeur");
  champValeur.value = valeurActuelle;
  _brancherAidePointage();
  champValeur.addEventListener("keydown", (ev) => { ev.stopPropagation(); if(ev.key === "Enter"){ ev.preventDefault(); document.getElementById("fusion-enregistrer").click(); } });
  document.getElementById("fusion-annuler").onclick = () => { _selection = null; _fermerBarreFusion(); };
  document.getElementById("fusion-assistant").onclick = () => _assistantFormuleFusion(table);
  document.getElementById("fusion-enregistrer").onclick = async () => {
    if(vueReordonnee){ notifierErreur(tj("engine/static/engine/js/index_1.js:1904:40")); return; }
    try {
      await _enregistrerFusion(table, slug, fusionId, document.getElementById("fusion-nature").value, champValeur.value);
      notifierSucces(tj("engine/static/engine/js/index_1.js:1907:23"));
    } catch(e) { /* déjà notifié par api() */ }
  };
  document.getElementById("fusion-defusionner").onclick = async () => {
    try {
      const resDel = await api(`/api/tables/${table.id}/fusions/${fusionId}/`, {method:"DELETE"});
      _majBadgeDenaturee(table, resDel);
      notifierSucces(tj("engine/static/engine/js/index_1.js:1914:23"));
      _selection = null; _fermerBarreFusion();
      afficherTable(slug);
    } catch(e) { /* déjà notifié par api() */ }
  };
}

// Choix d'opérateur repris de FORMULE_CHAMP_OPERATEUR_CHOICES (models.py),
// dupliqué ici comme FusionCelluleTable_TYPE_CHOICES ci-dessus — partagé par
// la formule de colonne (bouton ∑) ET la formule de ligne (saisie "=").
const FORMULE_OPERATEUR_CHOICES = [
  ["somme",tj("engine/static/engine/js/index_1.js:1925:13")], ["difference",tj("engine/static/engine/js/index_1.js:1925:41")], ["produit",tj("engine/static/engine/js/index_1.js:op_produit")], ["quotient",tj("engine/static/engine/js/index_1.js:op_quotient")],
  ["moyenne",tj("engine/static/engine/js/index_1.js:1926:15")], ["pourcentage","Pourcentage"], ["min",tj("engine/static/engine/js/index_1.js:1926:65")], ["max",tj("engine/static/engine/js/index_1.js:1926:84")], ["concatenation",tj("engine/static/engine/js/index_1.js:1926:113")],
  ["matrice",tj("engine/static/engine/js/index_1.js:1927:15")],
  ["programme",tj("engine/static/engine/js/index_1.js:1928:17")],
];
// Colonnes opérandes : le MÊME sélecteur graphique que les tables de calcul (_ouvrirSelecteurCible, cible_type « table » : une ou plusieurs
// zones, colonnes entières ou plages). Deux comportements, selon la sélection :
//  • uniquement des colonnes entières → calcul ligne par ligne sur les colonnes touchées (l'ordre de sélection est conservé) ;
//  • au moins une plage de lignes → mode « zones » : l'opérateur agrège TOUTES les cellules des zones (comme =SOMME(B2:B9; D1:D4)),
//    le résultat est unique et écrit dans la cible (parametres.zones, lu par calc_champ._zones_agregat).
// Résout { slugs, zones } ; zones est null tant qu'aucune plage de lignes n'est choisie.
const _TYPES_OPERANDES = ["nombre","decimal","monnaie","pourcentage","barre_progression","note","formule"];  // = TYPES_NUMERIQUES serveur (+ colonnes calculées)
// Sélection déjà faite sur la grille (plage de cellules, vue non triée/filtrée) : proposée en premier, converties en zones chronologiques.
async function _operandesDepuisGrille(dispo){
  const g = _grilleCourante, s = _selection;
  if(!g || !s || s.slug !== g.slug || g.vueReordonnee || g.total == null || (s.r1 === s.r2 && s.c1 === s.c2)) return null;
  const cols = []; for(let c = Math.min(s.c1, s.c2); c <= Math.max(s.c1, s.c2); c++) cols.push(g.champs[c]);
  if(!cols.every(c => c && dispo.some(d => d.slug === c.slug) && _TYPES_OPERANDES.includes(c.type_champ || c.type))) return null;
  const rMin = Math.min(s.r1, s.r2), rMax = Math.max(s.r1, s.r2);
  if(!await GY.confirmer(tj("engine/static/engine/js/index_1.js:1944:16", {v1: cols.length, v2: rMax - rMin + 1}))) return null;
  return {slugs: cols.map(c => c.slug), zones: cols.map(c => ({slug: c.slug, zone: "a", debut: g.total - rMax, fin: g.total - rMin}))};
}
async function _choisirOperandes(table, dispo, sansGrille){
  const direct = sansGrille ? null : await _operandesDepuisGrille(dispo);
  if(direct) return Promise.resolve(direct);
  return new Promise((resolve) => {
    _ouvrirSelecteurCible(null, table, dispo, {cible_type: "table", types_cibles_compatibles: _TYPES_OPERANDES}, (selections) => {
      const valides = selections.filter(sel => dispo[sel.colIndex] && dispo[sel.colIndex].slug);
      const slugs = [...new Set(valides.map(sel => dispo[sel.colIndex].slug))];
      if(!slugs.length){ notifierErreur(tj("engine/static/engine/js/index_1.js:1954:42")); return false; }
      const avecPlage = valides.some(sel => sel.ligneDebut !== null && sel.ligneDebut !== undefined);
      const zones = avecPlage ? valides.map(sel => ({
        slug: dispo[sel.colIndex].slug, zone: sel.zone || "a",
        debut: sel.ligneDebut === null || sel.ligneDebut === undefined ? null : sel.ligneDebut + 1,
        fin: sel.ligneFin === null || sel.ligneFin === undefined ? null : sel.ligneFin + 1,
      })) : null;
      if(_selCibleEtat) _selCibleEtat.surAnnulation = null;
      resolve({slugs, zones});
    }).then(() => { if(_selCibleEtat) _selCibleEtat.surAnnulation = () => resolve(null); });
  });
}
// ── Programme de formules : briques enchaînées + conditions (si / sinon si / sinon) + boucles (moteur : engine/formules_programme.py) ──
// Éditeur visuel UNIQUE, partagé avec l'onglet des calculs et les KPI (programme_kpi.js) : cette fenêtre ne fait que l'héberger.
// Briques = opérateurs de la table dynamique + bibliothèque de formules (GET /api/formules-calcul/). Résout { operateur:"programme", operandes_slugs, parametres:{programme} }.
async function _ouvrirConstructeurProgramme(table, champsDisponibles, slugsExclus){
  let bibliotheque = [];
  try { bibliotheque = (await api("/api/formules-calcul/")).formules || []; } catch(e) { /* briques de base seulement */ }
  const champs = champsDisponibles.filter(c => !slugsExclus.includes(c.slug));
  const fond = document.createElement("div");
  fond.style.cssText = "position:fixed;inset:0;background:color-mix(in srgb,var(--voile,#000000) 75%,transparent);display:flex;align-items:center;justify-content:center;z-index:2100;padding:16px";
  fond.innerHTML = `<div style="width:760px;max-width:100%;max-height:92vh;overflow-y:auto;background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:18px">
    <h2 style="margin:0 0 4px;font-size:15px">${tj("engine/static/engine/js/index_1.js:2015:50")}</h2>
    <p style="color:var(--muted);margin:0 0 8px;font-size:12px">${tj("engine/static/engine/js/index_1.js:2016:68")}</p>
    <div class="prog-editeur"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px"><button type="button" data-a="annuler">${tj("engine/static/engine/js/index_1.js:2020:122")}</button><button type="button" data-a="valider" class="principal">${tj("engine/static/engine/js/index_1.js:2020:195")}</button></div></div>`;
  document.body.appendChild(fond);
  const editeur = ProgrammeKPI.monter(fond.querySelector(".prog-editeur"), champs.map(c => ({slug: c.slug, nom: c.nom})), [], {
    operateurs: FORMULE_OPERATEUR_CHOICES.filter(o => o[0] !== "programme" && o[0] !== "matrice").map(o => [o[0], o[1]]),
    bibliotheque: bibliotheque.map(f => ({id: f.id, nom: f.nom})),
  });
  return new Promise((resolve) => {
    const fin = (res) => { fond.remove(); resolve(res); };
    fond.addEventListener("mousedown", (ev) => { if(ev.target === fond) fin(null); });
    fond.addEventListener("click", (ev) => {
      const a = ev.target.closest("button[data-a=annuler], button[data-a=valider]")?.dataset.a;
      if(a === "annuler") return fin(null);
      if(a !== "valider") return;
      const programme = editeur.lire();
      if(!programme.length){ notifierErreur(tj("engine/static/engine/js/index_1.js:2049:43")); return; }
      const utilises = ProgrammeKPI.references(programme);
      fin({operateur: "programme", operandes_slugs: champs.map(c => c.slug).filter(sl => utilises.has(sl)), parametres: {programme}});
    });
  });
}
async function _promptFormule(table, champsDisponibles, slugsExclus, opts){
  const ops = FORMULE_OPERATEUR_CHOICES.filter(o => !opts || !opts.ops || opts.ops.includes(o[0]));   // cellule fusionnée : seulement les opérateurs qu'une cellule peut évaluer
  const liste = ops.map((o,i) => `${i+1}. ${o[1]}`).join("\n");
  const choix = parseInt(await GY.demander(tj("engine/static/engine/js/index_1.js:2065:34", {v1: liste}), "1"), 10) - 1;
  const op = ops[choix];
  if(!op) return null;
  if(op[0] === "programme") return _ouvrirConstructeurProgramme(table, champsDisponibles, slugsExclus);
  if(op[0] === "concatenation") return { operateur: op[0], operandes_slugs: [] };
  const dispo = champsDisponibles.filter(c => !slugsExclus.includes(c.slug));
  const operandes = await _choisirOperandes(table, dispo, opts && opts.sansGrille);
  if(!operandes) return null;
  const res = { operateur: op[0], operandes_slugs: operandes.slugs };
  if(operandes.zones) res.parametres = { zones: operandes.zones };
  return res;
}
// Cellule fusionnée : MÊME éditeur visuel que les colonnes (opérateur + sélecteur de cible), traduit en expression « =… » du moteur
// de fusion_cellule.py (SOMME/MOYENNE/MIN/MAX/PRODUIT, colonne [Nom], plage B1:B5) — le champ reste modifiable à la main.
const _FUSION_FONCTIONS = {somme: "SOMME", moyenne: "MOYENNE", min: "MIN", max: "MAX", produit: "PRODUIT"};
function _expressionFusion(table, res){
  const cols = (_grilleCourante && _grilleCourante.champs) || table.champs;
  const nom = (sl) => { const n = (table.champs.find(c => c.slug === sl) || {}).nom; return n && !n.includes("]") ? n : sl; };   // le moteur accepte aussi l'identifiant
  let introuvable = false;
  const refs = res.parametres && res.parametres.zones
    ? res.parametres.zones.map(z => {
        if(z.debut == null && z.fin == null) return `[${nom(z.slug)}]`;
        const i = cols.findIndex(c => c.slug === z.slug);
        if(i < 0){ introuvable = true; return ""; }   // jamais de colonne « A » par défaut : la plage porterait sur une autre colonne
        const L = _lettreColonne(i), tot = (_paginationEtat[table.slug] || {}).total || 1;
        const d = z.debut == null ? 1 : z.debut, f = z.fin == null ? tot : z.fin;
        return d === f ? `${L}${d}` : `${L}${d}:${L}${f}`;
      })
    : res.operandes_slugs.map(sl => `[${nom(sl)}]`);
  if(introuvable) return null;
  if(res.operateur === "difference") return "=" + refs.map(r => `SOMME(${r})`).join("-");
  if(res.operateur === "quotient") return "=" + refs.map(r => `SOMME(${r})`).join("/");
  return `=${_FUSION_FONCTIONS[res.operateur]}(${refs.join("; ")})`;
}
async function _assistantFormuleFusion(table){
  const res = await _promptFormule(table, table.champs, [], {ops: ["somme","moyenne","min","max","produit","difference","quotient"], sansGrille: true});
  const champ = document.getElementById("fusion-valeur");
  if(!res || !champ) return;   // choix abandonné, ou barre refermée pendant le choix
  const expr = _expressionFusion(table, res);
  if(!expr){ notifierErreur(tj("engine/static/engine/js/index_1.js:a:fusion_pointage:colonne_introuvable")); return; }
  champ.value = expr;
  const n = document.getElementById("fusion-nature"); if(n) n.value = "formule";
  _majAidePointage();
}
async function _configurerFormuleLigne(table, ligne, champ, champs, slug){
  const res = await _promptFormule(table, champs, [champ.slug]);
  if(!res) return;
  try {
    await api(`/api/tables/${table.id}/lignes/${ligne._id}/formule-ligne/`, {
      method:"POST", body: JSON.stringify({...res, champs_cibles_ids:[champ.id]}),
    });
    notifierSucces(tj("engine/static/engine/js/index_1.js:2084:21"));
    afficherTable(slug);
  } catch(e) { /* déjà notifié par api() */ }
}
document.addEventListener("click", async function(e){
  const btn = e.target.closest(".btn-formule-champ");
  if(!btn) return;
  e.stopPropagation();
  const table = tables.find(t => t.slug === btn.dataset.tableSlug);
  const champ = table && table.champs.find(c => c.slug === btn.dataset.champSlug);
  if(!table || !champ) return;
  const res = await _promptFormule(table, table.champs, [champ.slug]);
  if(!res) return;
  try {
    await api(`/api/tables/${table.id}/champs/${champ.id}/formule/`, { method:"POST", body: JSON.stringify(res) });
    notifierSucces(tj("engine/static/engine/js/index_1.js:2099:21"));
    afficherTable(table.slug);
  } catch(err) { /* déjà notifié par api() */ }
});

// ── Transformation de colonne par regex (façon Power BI : « Remplacer les valeurs », « Extraire », « Mettre en forme ») ──
// API : POST /api/tables/<id>/champs/<champ_id>/transformer/ (apercu:true = aucune écriture), GET .../transformations/,
// POST /api/transformations-colonne/<id>/annuler/ (engine/views_transformation.py).
const _TRANSFO_OPERATIONS = [
  ["remplacer", tj("engine/static/engine/js/index_1.js:2108:18")], ["fusionner", tj("engine/static/engine/js/index_1.js:op_fusionner")], ["supprimer", tj("engine/static/engine/js/index_1.js:2108:71")], ["extraire", tj("engine/static/engine/js/index_1.js:2108:116")],
  ["majuscules", "MAJUSCULES"], ["minuscules", "minuscules"], ["titre", tj("engine/static/engine/js/index_1.js:2109:74")],
  ["rogner", tj("engine/static/engine/js/index_1.js:2110:15")], ["espaces", tj("engine/static/engine/js/index_1.js:2110:71")],
  ["prefixe", tj("engine/static/engine/js/index_1.js:2111:16")], ["suffixe", tj("engine/static/engine/js/index_1.js:2111:51")],
  ["cloner", tj("engine/static/engine/js/index_1.js:2112:15")], ["decouper", tj("engine/static/engine/js/index_1.js:2112:84")],
  ["geocoder", tj("engine/static/engine/js/index_1.js:2113:17")],
];
// Mode simple : l'utilisateur choisit une cible en français, le motif (expression régulière) est construit ici ; le serveur ne change pas.
const _CIBLES = [["texte", tj("engine/static/engine/js/index_1.js:2116:29"), 1], ["entre", tj("engine/static/engine/js/index_1.js:2116:62"), 2], ["avant", tj("engine/static/engine/js/index_1.js:2116:108"), 1], ["apres", tj("engine/static/engine/js/index_1.js:2116:151"), 1],
  ["chiffres", tj("engine/static/engine/js/index_1.js:2117:17"), 0], ["lettres", tj("engine/static/engine/js/index_1.js:2117:49"), 0], ["espaces", tj("engine/static/engine/js/index_1.js:2117:80"), 0], ["parentheses", tj("engine/static/engine/js/index_1.js:2117:115"), 0]];
const _echapper = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
function _motifSimple(cible, t1, t2, inclure, garder){   // -> {motif, groupe} ; motif vide = champs à compléter
  const fixes = {chiffres: "\\d+", lettres: "[^\\W\\d_]+", espaces: "\\s+", parentheses: "\\([^)]*\\)"};
  if(fixes[cible]) return {motif: fixes[cible], groupe: 0};
  const a = _echapper(t1), b = _echapper(t2);
  if(!t1 || (cible === "entre" && !t2)) return {motif: "", groupe: 0};
  if(cible === "texte") return {motif: a, groupe: 0};
  const m = {
    entre: garder ? `${a}(.*?)${b}` : inclure ? `${a}.*?${b}` : `(?<=${a}).*?(?=${b})`,
    avant: garder ? `^(.*?)${a}` : inclure ? `^.*?${a}` : `^.*?(?=${a})`,
    apres: garder ? `${a}(.*)$` : inclure ? `${a}.*$` : `(?<=${a}).*$`}[cible];
  return {motif: m, groupe: garder ? 1 : 0};
}
const _opCree = (op) => op === "cloner" || op === "decouper" || op === "geocoder";   // ajoutent des colonnes : impossible dans un groupe (structure commune)
const _opDerive = (op) => op === "cloner" || op === "decouper";   // crée des colonnes (POST …/deriver/) au lieu de modifier celle-ci
document.addEventListener("click", function(e){
  const btn = e.target.closest(".btn-transformer-champ");
  if(!btn) return;
  e.stopPropagation();
  const table = tables.find(t => t.slug === btn.dataset.tableSlug);
  const champ = table && table.champs.find(c => c.slug === btn.dataset.champSlug);
  if(table && champ) ouvrirModalTransformation(table, champ);
});

// ── Couleur de colonne ──────────────────────────────────────────────────────────────────────────────────────────
// 🎨 dans l'en-tête : la couleur se donne par un NOM (bleu, bordeaux…), un code HEXADÉCIMAL (#1e90ff) ou la PALETTE ; elle colore
// l'en-tête et les cellules de la colonne à l'écran ET dans les PDF (liste de table, résumé général). API (engine/views_couleur.py) :
// GET /api/couleurs-colonne/ (palette + noms connus), POST /api/tables/<id>/champs/<champ_id>/couleur/ {couleur} ("" = retirer).
function _texteSurCouleur(hex){ return GYCouleur.texteSur(hex); }
// Couleur d'un champ de formulaire = la couleur de sa colonne (ChampDynamique.couleur) : le libellé du champ prend la couleur, le bouton 🎨 la change.
function _styleLabelChamp(c){ return c.couleur ? ` style="background:${c.couleur};color:${_texteSurCouleur(c.couleur)};padding:2px 8px;border-radius:4px;display:inline-block"` : ""; }
function _btnCouleurChamp(table, c){ return ` <button type="button" class="btn-couleur-champ" data-table-slug="${escapeHtml(table.slug)}" data-champ-slug="${escapeHtml(c.slug)}" title="${tj("engine/static/engine/js/index_1.js:a:2162:185")}" style="font-size:10px;padding:0 5px;border:1px solid var(--border);border-radius:3px;background:transparent;color:var(--muted);cursor:pointer;vertical-align:middle;text-transform:none">🎨</button>`; }   // règle commune : static/engine/js/selecteur_couleur.js
document.addEventListener("click", function(e){
  const btn = e.target.closest(".btn-couleur-champ");
  if(!btn) return;
  e.stopPropagation();
  const table = tables.find(t => t.slug === btn.dataset.tableSlug);
  const champ = table && table.champs.find(c => c.slug === btn.dataset.champSlug);
  if(table && champ) ouvrirModalCouleur(table, champ);
});

async function ouvrirModalCouleur(table, champ){
  const couleur = await GYCouleur.choisir({titre: tj("engine/static/engine/js/index_1.js:2160:52", {v1: champ.nom}), valeur: champ.couleur, apercu: champ.nom});
  if(couleur === null) return;
  try {
    const r = await api(`/api/tables/${table.id}/champs/${champ.id}/couleur/`, { method:"POST", body: JSON.stringify({couleur}) });
    champ.couleur = r.couleur;
    notifierSucces(r.couleur ? tj("engine/static/engine/js/index_1.js:2165:33", {v1: champ.nom}) : tj("engine/static/engine/js/index_1.js:2165:71", {v1: champ.nom}));
    document.querySelectorAll(`.champ-item[data-champ-slug="${champ.slug}"] > label`).forEach(l => {   // formulaires déjà affichés (dont la fenêtre d'une ligne)
      l.style.background = champ.couleur || ""; l.style.color = champ.couleur ? _texteSurCouleur(champ.couleur) : "";
      l.style.padding = champ.couleur ? "2px 8px" : ""; l.style.borderRadius = champ.couleur ? "4px" : ""; l.style.display = champ.couleur ? "inline-block" : "";
    });
    afficherTable(table.slug);
  } catch(err) { /* api() a déjà signalé l'erreur */ }
}

function ouvrirModalTransformation(table, champ, initial){
  document.getElementById("modal-transfo-fond")?.remove();
  const fond = document.createElement("div");
  fond.id = "modal-transfo-fond";
  fond.style.cssText = "position:fixed;inset:0;background:color-mix(in srgb,var(--voile,#000000) 75%,transparent);display:flex;align-items:center;justify-content:center;z-index:2000;padding:16px";
  const champStyle = "padding:6px 8px;font-size:13px;width:100%;box-sizing:border-box";
  fond.innerHTML = `
    <div style="width:640px;max-width:100%;max-height:92vh;overflow-y:auto;background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:18px">
      <h2 style="margin:0 0 4px;font-size:15px">${tj("engine/static/engine/js/index_1.js:2182:50")} ${escapeHtml(champ.nom)} »</h2>
      ${table.groupe_id ? `<p id="transfo-groupe-info" style="margin:0 0 10px;padding:7px 10px;font-size:12px;border:1px solid var(--border);border-left:3px solid var(--accent, #4a90d9);border-radius:6px">${tj("engine/static/engine/js/index_1.js:2183:207")} <b>groupe</b> ${tj("engine/static/engine/js/index_1.js:2183:250")} ${escapeHtml(champ.nom)} ${tj("engine/static/engine/js/index_1.js:2183:328")} <b>${tj("engine/static/engine/js/index_1.js:2183:355")}</b> ${tj("engine/static/engine/js/index_1.js:2183:387")}</p>` : ""}<p style="color:var(--muted);margin:0 0 12px;font-size:12px">${tj("engine/static/engine/js/index_1.js:2183:513")}</p>
      <label style="font-size:12px">${tj("engine/static/engine/js/index_1.js:2184:38")}
        <select id="transfo-operation" style="${champStyle}">${_TRANSFO_OPERATIONS.filter(([v]) => !table.groupe_id || !_opCree(v)).map(([v,l]) => `<option value="${v}">${escapeHtml(l)}</option>`).join("")}</select></label>
      <div id="transfo-bloc-motif" style="margin-top:10px">
        <div id="transfo-bloc-simple">
          <label style="font-size:12px">${tj("engine/static/engine/js/index_1.js:2188:42")}
            <select id="transfo-cible" style="${champStyle}"></select></label>
          <div style="display:flex;gap:8px;margin-top:6px"><input id="transfo-t1" type="text" style="${champStyle}" autocomplete="off"><input id="transfo-t2" type="text" style="${champStyle}" autocomplete="off"></div>
          <label id="transfo-bloc-inclure" style="display:block;margin-top:6px;font-size:12px"><input id="transfo-inclure" type="checkbox" checked> ${tj("engine/static/engine/js/index_1.js:2191:150")}</label>
        </div>
        <label style="display:none"><input id="transfo-avance" type="checkbox"> ${tj("engine/static/engine/js/index_1.js:2193:82")}</label>
        <label id="transfo-bloc-regex" style="font-size:12px;display:none">${tj("engine/static/engine/js/index_1.js:2194:77")}
          <input id="transfo-motif" type="text" placeholder="ex. (\\d+)-(\\d+)  ou  ^\\s+|\\s+$" style="${champStyle};font-family:monospace" autocomplete="off"></label>
        <div style="display:flex;gap:14px;flex-wrap:wrap;margin-top:6px;font-size:12px">
          <label><input id="transfo-regex" type="checkbox" checked> ${tj("engine/static/engine/js/index_1.js:2197:70")}</label>
          <label><input id="transfo-casse" type="checkbox"> ${tj("engine/static/engine/js/index_1.js:2198:62")}</label>
          <label><input id="transfo-tout" type="checkbox" checked> ${tj("engine/static/engine/js/index_1.js:2199:69")}</label>
          <label id="transfo-bloc-dernier" style="display:none"><input id="transfo-dernier" type="checkbox"> ${tj("engine/static/engine/js/index_1.js:2200:111")}</label>
        </div>
        <div id="transfo-aide-decoupe" style="display:none;margin-top:6px;font-size:11px;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:2202:111")} <code>-</code> ou <code>\\s+</code>${tj("engine/static/engine/js/index_1.js:2202:178")} <code>(\\d+)(\\D+)</code>${tj("engine/static/engine/js/index_1.js:2202:261")}</div>
      </div>
      <div id="transfo-bloc-remplacement" style="margin-top:10px">
        <label style="font-size:12px">${tj("engine/static/engine/js/index_1.js:2205:40")} <span style="color:var(--muted)">${tj("engine/static/engine/js/index_1.js:2205:87")}</span>
          <input id="transfo-remplacement" type="text" style="${champStyle}" autocomplete="off"></label>
      </div>
      <div id="transfo-bloc-fusion" style="margin-top:10px;display:none">
        <label style="font-size:12px">${tj("engine/static/engine/js/index_1.js:fusion_termes")} <span style="color:var(--muted)">${tj("engine/static/engine/js/index_1.js:fusion_termes_aide")}</span>
          <textarea id="transfo-termes" rows="4" style="${champStyle}" autocomplete="off"></textarea></label>
        <label style="display:block;margin-top:6px;font-size:12px">${tj("engine/static/engine/js/index_1.js:fusion_final")}
          <input id="transfo-final" type="text" style="${champStyle}" autocomplete="off"></label>
      </div>
      <div id="transfo-bloc-extraire" style="margin-top:10px;display:none;gap:10px;flex-wrap:wrap;align-items:end">
        <label style="font-size:12px">${tj("engine/static/engine/js/index_1.js:2209:40")} <span style="color:var(--muted)">${tj("engine/static/engine/js/index_1.js:2209:89")}</span>
          <input id="transfo-groupe" type="number" min="0" value="1" style="${champStyle};width:90px"></label>
        <label style="font-size:12px">${tj("engine/static/engine/js/index_1.js:2211:40")}
          <select id="transfo-si-absent" style="${champStyle}"><option value="garder">${tj("engine/static/engine/js/index_1.js:2212:88")}</option><option value="vider">${tj("engine/static/engine/js/index_1.js:2212:149")}</option></select></label>
      </div>
      <div id="transfo-bloc-texte" style="margin-top:10px;display:none">
        <label style="font-size:12px">${tj("engine/static/engine/js/index_1.js:2215:40")}
          <input id="transfo-texte" type="text" style="${champStyle}" autocomplete="off"></label>
      </div>
      <div id="transfo-resume" style="margin-top:12px;font-size:12px;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:2218:91")}</div>
      <div id="transfo-apercu" style="margin-top:6px;max-height:200px;overflow:auto"></div>
      <div id="transfo-historique" style="margin-top:12px"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
        <button id="transfo-fermer" type="button">${tj("engine/static/engine/js/index_1.js:2222:52")}</button>
        <button id="transfo-appliquer" type="button" class="principal" disabled>${table.groupe_id ? tj("engine/static/engine/js/index_1.js:2223:102") : tj("engine/static/engine/js/index_1.js:2223:133")}</button>
      </div>
    </div>`;
  document.body.appendChild(fond);
  const el = (id) => fond.querySelector("#" + id);
  const fermer = () => fond.remove();
  el("transfo-fermer").onclick = fermer;
  fond.addEventListener("mousedown", (ev) => { if(ev.target === fond) fermer(); });

  const urlBase = `/api/tables/${table.id}/champs/${champ.id}`;
  const _simple = () => {   // motif construit depuis le mode simple (null en mode avancé ou si l'opération n'a pas de motif)
    const op = el("transfo-operation").value;
    return !el("transfo-avance").checked && besoinMotif(op) ? _motifSimple(el("transfo-cible").value, el("transfo-t1").value, el("transfo-t2").value, el("transfo-inclure").checked, op === "extraire") : null;
  };
  const _termesSaisis = () => { const v = el("transfo-termes").value; return (v.includes("\n") ? v.split("\n") : v.split(/[,;]/)).map(s => s.trim()).filter(Boolean); };   // un terme par ligne, ou séparés par , ou ;
  const params = () => { const sm = _simple(), fus = el("transfo-operation").value === "fusionner"; return {
    operation: el("transfo-operation").value, motif: sm ? sm.motif : el("transfo-motif").value,
    est_regex: sm ? true : el("transfo-regex").checked, casse: !fus && el("transfo-casse").checked, tout: el("transfo-tout").checked,
    termes: fus ? _termesSaisis() : undefined,
    remplacement: fus ? el("transfo-final").value : sm ? el("transfo-remplacement").value.replace(/\\/g, "\\\\").replace(/\$/g, "$$$$") : el("transfo-remplacement").value,   // texte tel quel : « \ » et « $ » ne sont pas des codes
    groupe: sm ? sm.groupe : parseInt(el("transfo-groupe").value, 10) || 0,
    si_absent: el("transfo-si-absent").value, texte: el("transfo-texte").value,
    mode: el("transfo-operation").value, dernier: el("transfo-dernier").checked,
  }; };
  const urlOp = (op) => `${urlBase}/${_opDerive(op) ? "deriver" : "transformer"}/`;
  const besoinMotif = (op) => ["remplacer", "supprimer", "extraire", "decouper"].includes(op);
  const adapterFormulaire = () => {
    const op = el("transfo-operation").value;
    el("transfo-bloc-motif").style.display = besoinMotif(op) ? "" : "none";
    el("transfo-bloc-remplacement").style.display = op === "remplacer" ? "" : "none";
    el("transfo-bloc-fusion").style.display = op === "fusionner" ? "" : "none";
    el("transfo-bloc-dernier").style.display = el("transfo-aide-decoupe").style.display = op === "decouper" ? "" : "none";
    el("transfo-bloc-extraire").style.display = op === "extraire" ? "flex" : "none";
    el("transfo-bloc-texte").style.display = (op === "prefixe" || op === "suffixe") ? "" : "none";
    const av = el("transfo-avance").checked, sel = el("transfo-cible"), ancienne = sel.value;
    const permises = _CIBLES.filter(c => op !== "decouper" || ["texte", "espaces"].includes(c[0]));   // une découpe coupe sur un séparateur
    if(sel.options.length !== permises.length) sel.innerHTML = permises.map(c => `<option value="${c[0]}">${escapeHtml(c[1])}</option>`).join("");
    if(permises.some(c => c[0] === ancienne)) sel.value = ancienne;
    const besoin = (_CIBLES.find(c => c[0] === sel.value) || [0, 0, 0])[2];
    el("transfo-bloc-simple").style.display = av ? "none" : "";
    el("transfo-bloc-regex").style.display = av ? "" : "none";
    el("transfo-regex").parentElement.style.display = el("transfo-groupe").parentElement.style.display = av ? "" : "none";
    el("transfo-aide-decoupe").style.display = av && op === "decouper" ? "" : "none";
    el("transfo-t1").style.display = besoin >= 1 ? "" : "none"; el("transfo-t2").style.display = besoin >= 2 ? "" : "none";
    el("transfo-t1").placeholder = besoin === 2 ? tj("engine/static/engine/js/index_1.js:2264:52") : tj("engine/static/engine/js/index_1.js:2264:71"); el("transfo-t2").placeholder = tj("engine/static/engine/js/index_1.js:2264:114");
    el("transfo-bloc-inclure").style.display = ["entre", "avant", "apres"].includes(sel.value) && (op === "remplacer" || op === "supprimer") ? "block" : "none";
  };

  let minuterie = null, numeroApercu = 0, apercuValide = false;
  const calculerApercu = async () => {
    const p = params(), op = p.operation, mon = ++numeroApercu;
    if(op === "geocoder"){   // pas d'aperçu à la frappe : chaque lieu coûte une recherche (≈1 s) ; le travail se fait à « Appliquer », par lots
      el("transfo-apercu").innerHTML = "";
      el("transfo-resume").textContent = tj("engine/static/engine/js/index_1.js:2273:43");
      apercuValide = true; el("transfo-appliquer").disabled = false; return;
    }
    const incomplet = (besoinMotif(op) && !p.motif) || ((op === "prefixe" || op === "suffixe") && !p.texte) || (op === "fusionner" && (!p.termes.length || !p.remplacement.trim()));
    apercuValide = false; el("transfo-appliquer").disabled = true;
    if(incomplet){ el("transfo-resume").textContent = tj("engine/static/engine/js/index_1.js:2278:56"); el("transfo-apercu").innerHTML = ""; return; }
    try {
      // fetch direct : api() afficherait une notification d'erreur à chaque frappe d'un motif encore incomplet.
      const r = await fetch(urlOp(op), { method:"POST", headers:{"X-CSRFToken": getCookie("gy_csrftoken"), "Content-Type":"application/json"}, body: JSON.stringify({...p, apercu:true}) });
      const d = await r.json().catch(() => ({erreur: r.statusText}));
      if(mon !== numeroApercu) return;   // une frappe plus récente a relancé l'aperçu
      if(!r.ok){ el("transfo-resume").innerHTML = `<span style="color:var(--texte-erreur,#ff8a80)">${escapeHtml(d.erreur || tj("engine/static/engine/js/index_1.js:2284:106"))}</span>`; el("transfo-apercu").innerHTML = ""; return; }
      const notes = [];
      if(d.vides_ignorees) notes.push(tj("engine/static/engine/js/index_1.js:2286:40", {v1: d.vides_ignorees}));
      if(d.non_concernees) notes.push(`${tj("engine/static/engine/js/index_1.js:2577:40", {v1: d.non_concernees})}`);
      if(d.refusees_par_le_type) notes.push(tj("engine/static/engine/js/index_1.js:2287:46", {v1: d.refusees_par_le_type}));
      if(d.trop_longues_ou_lentes) notes.push(tj("engine/static/engine/js/index_1.js:2288:48", {v1: d.trop_longues_ou_lentes}));
      el("transfo-resume").innerHTML = `<b style="color:var(--text)">${d.modifiees}</b> ${tj("engine/static/engine/js/index_1.js:2289:90")} ${d.total_lignes} ${_opDerive(op) ? `non vide(s) alimenteront ${op === "cloner" ? tj("engine/static/engine/js/index_1.js:2289:187") : tj("engine/static/engine/js/index_1.js:2289:210")}` : tj("engine/static/engine/js/index_1.js:2289:237")}${d.groupe ? tj("engine/static/engine/js/index_1.js:2289:269", {v1: d.groupe.tables.length, v2: escapeHtml(d.groupe.tables.join(", "))}) : ""}${notes.length ? " · " + escapeHtml(notes.join(" · ")) : ""}.`;
      el("transfo-apercu").innerHTML = d.exemples.length
        ? `<table style="width:100%;font-size:12px;border-collapse:collapse"><thead><tr><th style="text-align:left;padding:3px 6px">${tj("engine/static/engine/js/index_1.js:2582:133")}</th><th style="text-align:left;padding:3px 6px">${tj("engine/static/engine/js/index_1.js:2291:188")}</th></tr></thead><tbody>${d.exemples.slice(0, 10).map(x => `<tr><td style="padding:3px 6px;border-top:1px solid var(--border);overflow-wrap:break-word">${escapeHtml(x.avant)}</td><td style="padding:3px 6px;border-top:1px solid var(--border);overflow-wrap:break-word">${escapeHtml(x.apres)}</td></tr>`).join("")}</tbody></table>`
        : "";
      apercuValide = d.modifiees > 0;
      el("transfo-appliquer").disabled = !apercuValide;
    } catch(err) { if(mon === numeroApercu) el("transfo-resume").textContent = tj("engine/static/engine/js/index_1.js:2295:81"); }
  };
  const planifierApercu = () => { clearTimeout(minuterie); minuterie = setTimeout(calculerApercu, 350); };
  ["transfo-operation","transfo-motif","transfo-regex","transfo-casse","transfo-tout","transfo-remplacement","transfo-groupe","transfo-si-absent","transfo-texte","transfo-dernier","transfo-cible","transfo-t1","transfo-t2","transfo-inclure","transfo-avance","transfo-termes","transfo-final"]
    .forEach(id => { el(id).addEventListener("input", planifierApercu); el(id).addEventListener("change", planifierApercu); });
  ["transfo-operation", "transfo-cible", "transfo-avance"].forEach(id => el(id).addEventListener("change", adapterFormulaire));
  if(initial && initial.termes){ el("transfo-operation").value = "fusionner"; el("transfo-termes").value = initial.termes.join("\n") + "\n"; }   // « \n » final : même un terme unique contenant une virgule n'est pas coupé   // venu de la liste des termes de l'analyse exploratoire
  adapterFormulaire();

  const chargerHistorique = async () => {
    try {
      const d = await (await fetch(`${urlBase}/transformations/`)).json();
      const liste = (d.transformations || []);
      el("transfo-historique").innerHTML = liste.length ? `<div style="font-size:12px;color:var(--muted);margin-bottom:4px">${tj("engine/static/engine/js/index_1.js:2307:126")}${table.groupe_id ? tj("engine/static/engine/js/index_1.js:2307:190") : ""}</div>` + liste.map(j => {
        const op = (_TRANSFO_OPERATIONS.find(o => o[0] === (j.operation || {}).operation) || [0, (j.operation || {}).operation || "?"])[1];
        return `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:12px;padding:3px 0;border-top:1px solid var(--border)"><span>${escapeHtml(new Date(j.cree_le).toLocaleString())} — ${escapeHtml(op)} · ${j.nb_modifiees} ${tj("engine/static/engine/js/index_1.js:2309:263")}${j.annule ? tj("engine/static/engine/js/index_1.js:2309:286") : ""}</span>${j.annulable ? `<button type="button" class="transfo-annuler" data-id="${j.id}">${tj("engine/static/engine/js/index_1.js:2309:392")}</button>` : ""}${j.refaisable ? `<button type="button" class="transfo-refaire" data-id="${j.id}">${tj("engine/static/engine/js/index_1.js:2309:497")}</button>` : ""}</div>`;
      }).join("") : "";
      el("transfo-historique").querySelectorAll(".transfo-annuler").forEach(b => b.onclick = async () => {
        if(!await GY.confirmer(tj("engine/static/engine/js/index_1.js:2312:22"))) return;
        b.disabled = true;
        const url = `/api/transformations-colonne/${b.dataset.id}/annuler/`;
        try {
          let r;
          try { r = await api(url, { method:"POST", body: JSON.stringify({}) }); }
          catch(err) { if(err.code !== "modifie_depuis" || !await GY.confirmer(tj("engine/static/engine/js/index_1.js:2318:70"))) throw err;
                       r = await api(url, { method:"POST", body: JSON.stringify({forcer:true}) }); }
          notifierSucces(r.colonnes_retirees ? tj("engine/static/engine/js/index_1.js:2320:49", {v1: r.colonnes_retirees.join(", ")}) : tj("engine/static/engine/js/index_1.js:2320:112", {v1: r.restaurees, v2: r.conservees_car_modifiees_depuis ? tj("engine/static/engine/js/index_1.js:2320:190", {v1: r.conservees_car_modifiees_depuis}) : ""}));
          fermer(); afficherTable(table.slug);
        } catch(err) { b.disabled = false; }
      });
      el("transfo-historique").querySelectorAll(".transfo-refaire").forEach(b => b.onclick = async () => {
        b.disabled = true;
        try {
          const r = await api(`/api/transformations-colonne/${b.dataset.id}/refaire/`, { method:"POST", body: JSON.stringify({}) });
          notifierSucces(r.colonnes ? tj("engine/static/engine/js/index_1.js:2328:40", {v1: r.colonnes.join(", ")}) : tj("engine/static/engine/js/index_1.js:2328:94", {v1: r.restaurees, v2: r.conservees_car_modifiees_depuis ? tj("engine/static/engine/js/index_1.js:2328:171", {v1: r.conservees_car_modifiees_depuis}) : ""}));
          fermer(); afficherTable(table.slug);
        } catch(err) { b.disabled = false; }
      });
    } catch(err) { /* l'historique est facultatif */ }
  };
  chargerHistorique();

  el("transfo-appliquer").onclick = async (ev) => {
    if(!apercuValide) return;
    const bouton = ev.target; bouton.disabled = true; bouton.textContent = "…";
    if(params().operation === "geocoder"){
      try {
        const ur = `${urlBase}/geocoder/`, post = (corps) => api(ur, { method:"POST", body: JSON.stringify(corps) });
        const l = await post({etape:"lieux"});
        if(!l.lieux.length){ el("transfo-resume").textContent = tj("engine/static/engine/js/index_1.js:2343:66"); bouton.disabled = false; bouton.textContent = tj("engine/static/engine/js/index_1.js:2343:157"); return; }
        const corr = {}; let fait = 0, trouves = 0;
        for(let i = 0; i < l.lieux.length; i += 15){
          const lot = l.lieux.slice(i, i + 15);
          const r = await post({etape:"resoudre", lieux: lot});
          for(const [lieu, ll] of Object.entries(r.resultats)){ fait++; if(ll){ corr[lieu] = ll; trouves++; } }
          el("transfo-resume").textContent = tj("engine/static/engine/js/index_1.js:2349:47", {v1: fait, v2: l.lieux.length, v3: trouves});
          if(r.erreur_service){ notifierErreur(r.erreur_service); break; }
        }
        if(!trouves){ el("transfo-resume").textContent = tj("engine/static/engine/js/index_1.js:2352:59"); bouton.disabled = false; bouton.textContent = tj("engine/static/engine/js/index_1.js:2352:176"); return; }
        const f = await post({etape:"appliquer", correspondances: corr});
        notifierSucces(tj("engine/static/engine/js/index_1.js:2354:25", {v1: f.nouvelles_colonnes[0], v2: f.modifiees, v3: f.lieux_convertis, v4: l.lieux_ignores ? tj("engine/static/engine/js/index_1.js:2354:158", {v1: l.lieux_ignores}) : "", v5: fait > trouves ? ` ${tj("engine/static/engine/js/index_1.js:2645:267", {v1: fait - trouves})}` : ""}));
        if(window.gyGrille) window.gyGrille.memoriserServeur(table, table.slug, tj("engine/static/engine/js/index_1.js:2355:82", {v1: champ.nom}));
        fermer(); afficherTable(table.slug);
      } catch(err) { bouton.disabled = false; bouton.textContent = tj("engine/static/engine/js/index_1.js:2357:69"); }
      return;
    }
    try {
      let r;
      try { r = await api(urlOp(params().operation), { method:"POST", body: JSON.stringify({...params(), apercu:false}) }); }
      catch(err) {   // trop volumineux pour être annulable : l'utilisateur confirme en connaissance de cause, ou renonce
        if(err.code !== "non_annulable" || !await GY.confirmer(err.message + tj("engine/static/engine/js/index_1.js:2364:68"))) throw err;
        r = await api(urlOp(params().operation), { method:"POST", body: JSON.stringify({...params(), apercu:false, confirmer_sans_annulation:true}) });
      }
      if(window.gyGrille && r.annulable !== false) window.gyGrille.memoriserServeur(table, table.slug, tj("engine/static/engine/js/index_1.js:2367:105", {v1: champ.nom}));
      notifierSucces(r.nouvelles_colonnes ? tj("engine/static/engine/js/index_1.js:2368:46", {v1: r.nouvelles_colonnes.join(", ")}) : tj("engine/static/engine/js/index_1.js:2368:108", {v1: r.modifiees, v2: champ.nom, v3: r.annulable ? "" : tj("engine/static/engine/js/index_1.js:2368:190")}));
      fermer(); afficherTable(table.slug);
    } catch(err) { bouton.disabled = false; bouton.textContent = tj("engine/static/engine/js/index_1.js:2370:67"); }
  };
  el("transfo-cible").focus();
}

// Champ « colonne » : remplit le menu avec les valeurs ACTUELLES de la colonne source (relues a chaque appel :
// une ligne ajoutee dans la table source apparait seule). Garde la valeur deja choisie meme si elle a disparu de la source.
async function _peuplerMenuColonne(sel){
  const d = sel.dataset;
  if(!d.tableId || !d.colonne || !d.nominale) return;
  d.charge = "1";
  try{
    const res = await api(`/api/tables/${d.tableId}/valeurs-colonne/?colonne=${encodeURIComponent(d.colonne)}&nominale=${encodeURIComponent(d.nominale)}`);
    const actuel = d.valeur !== undefined ? d.valeur : sel.value;  // lu APRES l'attente : deux appels concurrents restent coherents
    const vals = (res.valeurs || []).map(String);
    if(actuel && !vals.includes(String(actuel))) vals.push(String(actuel));
    const cle = vals.join("\u0001");
    if(d.cle !== cle){
      sel.innerHTML = '<option value="">—</option>' + vals.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
      d.cle = cle;
    }
    sel.value = actuel || "";
    delete d.valeur;
  }catch(e){ /* erreur deja notifiee par api() */ }
}
document.addEventListener("click", (ev) => {  // « ▾ » dans une cellule : meme chemin que le double-clic
  const b = ev.target.closest && ev.target.closest(".btn-menu-colonne");
  const td = b && b.closest("td.cellule-editable");
  if(td) td.dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
});
async function _editerCelluleColonne(td, table, slug, champ, ligne){
  if(td.querySelector(".edition-cellule")) return;
  const original = td.innerHTML, avant = ligne[champ.slug] ?? "";
  const sel = document.createElement("select");
  sel.className = "edition-cellule"; sel.style = "width:100%;padding:3px;font-size:13px";
  Object.assign(sel.dataset, {tableId: champ.relation_vers_id || "", colonne: champ.relation_colonne_source || "",
                              nominale: champ.relation_champ_affichage || "", valeur: String(avant)});
  sel.innerHTML = '<option value="">—</option>';
  td.innerHTML = ""; td.appendChild(sel); sel.focus();
  let fini = false;
  const fermer = async (enregistrer) => {
    if(fini) return; fini = true;
    const nouvelle = sel.value;
    if(enregistrer && String(nouvelle) !== String(avant)){
      try {
        await api(`/api/tables/${table.id}/lignes/${ligne._id}/modifier/`, {method:"POST", body: JSON.stringify({[champ.slug]: nouvelle})});
        if(window.gyGrille) window.gyGrille.memoriser(table, slug, [{ligneId: ligne._id, valeurs: {[champ.slug]: avant}}], "Modification");
        ligne[champ.slug] = nouvelle;
        td.innerHTML = valeurCellule(champ, nouvelle, null) + tj("engine/static/engine/js/index_1.js:2418:64");
      } catch(e) { td.innerHTML = original; }
    } else td.innerHTML = original;
    if(window.gyGrille) window.gyGrille.refocaliser();
  };
  sel.addEventListener("change", () => fermer(true));
  sel.addEventListener("blur", () => fermer(true));
  sel.addEventListener("keydown", (ev) => { ev.stopPropagation(); if(ev.key === "Escape"){ ev.preventDefault(); fermer(false); } });
  await _peuplerMenuColonne(sel);
  try { if(!fini && sel.showPicker) sel.showPicker(); } catch(e) { /* menu natif : ouverture au clic suivant */ }
}
function _demarrerEditionCellule(td, table, slug, champs, ligneParDispIndex, saisie){
  const r = parseInt(td.dataset.ligneIdx, 10), c = parseInt(td.dataset.colIdx, 10);
  const ligne = ligneParDispIndex[r];
  const champ = champs[c];
  if(!ligne || !champ || td.querySelector("input.edition-cellule")) return;
  if(champ.type === "colonne"){ _editerCelluleColonne(td, table, slug, champ, ligne); return; }
  if(saisie === "="){ _configurerFormuleLigne(table, ligne, champ, champs, slug); return; }
  const valeurActuelle = ligne[champ.slug];
  const contenuOriginal = td.innerHTML;
  const input = document.createElement("input");
  input.className = "edition-cellule";
  // Saisie directe (frappe d'un caractère sur une cellule sélectionnée, comme Excel) : champ texte pour
  // accepter le caractère tapé même dans une colonne numérique ou date ; le serveur valide le type.
  input.type = saisie !== undefined ? "text" : ["nombre"].includes(champ.type) ? "number" : (champ.type === "decimal" || champ.type === "monnaie" || champ.type === "pourcentage" || champ.type === "note") ? "number" : (champ.type === "date" ? "date" : champ.type === "heure" ? "time" : champ.type === "datetime" ? "datetime-local" : champ.type === "couleur" ? "color" : "text");
  if(input.type === "number") input.step = "any";
  input.value = saisie !== undefined ? saisie : (valeurActuelle ?? "");
  input.style = "width:100%;padding:3px;font-size:13px";
  td.innerHTML = ""; td.appendChild(input); input.focus();
  if(saisie === undefined) input.select();
  let deplacement = null;  // [dLigne, dColonne] après validation (Entrée, Tab)
  let termine = false;

  const valider = async () => {
    if(termine) return;
    termine = true;
    const nouvelle = window.gyGrille ? window.gyGrille.normaliser(champ, input.value) : input.value;
    const suite = () => { if(deplacement && window.gyGrille) window.gyGrille.deplacer(deplacement[0], deplacement[1]); else if(window.gyGrille) window.gyGrille.refocaliser(); };
    // Valeur inchangée : aucune requête (chaque modification crée un bloc d'audit et relance les formules).
    if(String(nouvelle) === String(valeurActuelle ?? "")){ td.innerHTML = contenuOriginal; suite(); return; }
    try {
      await api(`/api/tables/${table.id}/lignes/${ligne._id}/modifier/`, {
        method:"POST", body: JSON.stringify({[champ.slug]: nouvelle}),
      });
      if(window.gyGrille) window.gyGrille.memoriser(table, slug, [{ligneId: ligne._id, valeurs: {[champ.slug]: valeurActuelle ?? ""}}], "Modification");
      ligne[champ.slug] = nouvelle;
      td.innerHTML = valeurCellule(champ, nouvelle, null) + tj("engine/static/engine/js/index_1.js:2464:62");
      // Document à formules : une valeur surveillée a changé -> les cellules « =formule » sont recalculées.
      if(document.querySelector('.grille-tableur td[data-fusion-type="formule"]')){ afficherTable(slug); return; }
    } catch(e) { td.innerHTML = contenuOriginal; }
    suite();
  };
  input.addEventListener("keydown", (ev) => {
    ev.stopPropagation();  // les flèches / Suppr agissent dans le champ, pas sur la grille
    if(ev.key === "Enter"){ ev.preventDefault(); deplacement = [ev.shiftKey ? -1 : 1, 0]; input.blur(); }
    else if(ev.key === "Tab"){ ev.preventDefault(); deplacement = [0, ev.shiftKey ? -1 : 1]; input.blur(); }
    else if(ev.key === "Escape"){
      // Retirer l'écouteur AVANT de remplacer le contenu : retirer le champ du DOM déclenche « blur »,
      // qui enregistrait la saisie qu'on voulait justement abandonner.
      ev.preventDefault();
      termine = true;
      input.removeEventListener("blur", valider);
      td.innerHTML = contenuOriginal;
      if(window.gyGrille) window.gyGrille.refocaliser();
    }
  });
  // Saisie "=" en début de cellule, comme sur Excel : ouvre la formule de
  // ligne au lieu de committer "=" comme valeur littérale.
  input.addEventListener("input", () => {
    if(input.value !== "=") return;
    termine = true;
    input.removeEventListener("blur", valider);
    td.innerHTML = contenuOriginal;
    _configurerFormuleLigne(table, ligne, champ, champs, slug);
  });
  input.addEventListener("blur", valider, { once:true });
}
// Vue Dashboard
async function afficherTableauBord(slug){
  modeVue = "dashboard"; tableActive = slug; memoriserContexteAccueil();
  const table = tables.find(t => t.slug === slug);
  const data = await api(`/api/tables/${table.id}/tableau-bord/`);

  { const _et = document.getElementById("entete-titre"); if(_et) _et.textContent = _titreAnalyse(table); }
  rendreActionsTable(table, slug);

  const zone = document.getElementById("zone-contenu");
  zone.innerHTML = `
    <div class="zone-analyse">
      <textarea id="texte-nouvelle-analyse" placeholder="${tj("engine/static/engine/js/index_1.js:2798:58", {v1: escapeHtml(table.nom)})}"></textarea>
      <button class="principal" id="btn-creer-carte" style="margin-top:8px;width:100%">${tj("engine/static/engine/js/index_1.js:2508:89")}</button>
    </div>
    <div id="scripts-attente" style="max-width:900px;margin:0 auto 18px"></div>
    <div id="grille-dashboard-conteneur" style="width:100%"></div>
  `;

  document.getElementById("btn-creer-carte").onclick = () => creerCarte(slug);

  // Chaque demande "en_cours" traite désormais réellement sur un worker
  // Celery : au rechargement du dashboard, on ne suppose plus un plantage
  // serveur — on affiche la carte "en cours" et on reprend le polling
  // jusqu'à ce que le worker ait terminé (génération ou exécution).
  const zoneAttente = document.getElementById("scripts-attente");
  for(const d of (data.en_cours || [])){
    if(zoneAttente.querySelector(`[data-demande-id="${d.id}"]`)) continue;
    const carte = document.createElement("div");
    carte.className = tj("engine/static/engine/js/index_1.js:2524:24");
    carte.dataset.demandeId = d.id;
    carte.style.borderColor = "var(--accent)";
    carte.innerHTML = _cartePhaseEnCours(d.texte || d.intention || tj("engine/static/engine/js/index_1.js:2527:69"), tj("engine/static/engine/js/index_1.js:2527:80"));
    // Pas de bouton d'annulation ici : le traitement a démarré avant le
    // rechargement de la page, on ne peut plus l'interrompre côté front —
    // seule une suppression manuelle reste possible si ça traîne vraiment.
    const btnAnnulerReprise = carte.querySelector(".btn-annuler-generation");
    if(btnAnnulerReprise){
      btnAnnulerReprise.textContent = tj("engine/static/engine/js/index_1.js:2533:40");
      btnAnnulerReprise.onclick = async (ev) => {
        if(!await GY.confirmer(tj("engine/static/engine/js/index_1.js:2535:22"))) return;
        ev.target.textContent = "…"; ev.target.disabled = true;
        try {
          await api(`/api/demandes/${d.id}/supprimer/`, {method:"POST"});
          carte.remove();
          notifierSucces(tj("engine/static/engine/js/index_1.js:2540:27"));
          rafraichirTables();
        } catch(e) {
          ev.target.textContent = tj("engine/static/engine/js/index_1.js:2543:36"); ev.target.disabled = false;
        }
      };
    }
    zoneAttente.appendChild(carte);
    attendreFinDemande(d.id).then(demande => {
      carte.classList.remove("carte-en-cours");
      if(demande.statut === "script_genere"){
        carte.innerHTML = _cartePhaseScript(demande);
        _activerCMLectureSeule(carte);
        _brancher_lancer(carte, demande, slug);
      } else if(demande.statut === "echec"){
        carte.innerHTML = _cartePhaseEchec(demande);
        _brancher_fermer_echec(carte, demande, slug);
      } else {
        // "execute" : résultat final déjà calculé — carte d'analyse complète.
        _finaliserCarte(carte, demande, slug);
      }
    }).catch(()=>{ /* carte retirée entre-temps ou erreur réseau : rien à faire */ });
  }

  const conteneur = document.getElementById("grille-dashboard-conteneur");
  if(data.analyses.length === 0){
    conteneur.innerHTML = `<div style="color:var(--muted);max-width:600px;margin:30px auto;text-align:center">
      ${tj("engine/static/engine/js/index_1.js:2567:8")} ${escapeHtml(table.nom)} ${tj("engine/static/engine/js/index_1.js:2567:69")}</div>`;
    return;
  }

  conteneur.innerHTML = '<div class="grille-dashboard" style="width:100%"></div>';
  const grille = conteneur.querySelector(".grille-dashboard");
  // Les analyses arrivent triées -cree_le (plus récentes en premier).
  // On numérote dans l'ordre chronologique (Analyse 1 = la plus ancienne),
  // donc on parcourt en sens inverse pour attribuer les numéros.
  const total = data.analyses.length;
  for(let i = 0; i < data.analyses.length; i++){
    const a = data.analyses[i];
    const num = total - i;  // Analyse 1 = la plus ancienne
    // En-tête de groupe : séparateur visuel avec numéro
    const sep = document.createElement("div");
    sep.className = "analyse-groupe-header";
    sep.innerHTML = `<span class="analyse-num">${tj("engine/static/engine/js/index_1.js:2583:49")} ${num}</span><span class="analyse-sep-line"></span>`;
    grille.appendChild(sep);
    const c = a.statut === "proposee" ? construireCarteProposee(a, slug) : construireCarteAnalyse(a);
    c.dataset.analyseNum = num;
    // Badge numéro dans le titre de la carte
    const titreEl = c.querySelector(".titre-analyse");
    if(titreEl){
      const badge = document.createElement("span");
      badge.className = "badge-analyse-num";
      badge.textContent = `#${num}`;
      titreEl.prepend(badge);
    }
    grille.appendChild(c);
  }
}
// ── Carte unique : une demande naît, vit et meurt dans le même élément DOM ──
// Phases : EN COURS (génération) → script prêt (bouton Lancer) → exécution
//          → résultat (carte-analyse inline) ou erreur.
// Aucun rechargement du tableau de bord, aucune carte externe.
let _genererEnCours = false;
// Cet indicateur ne vit que le temps exact de l'appel IA (await synchrone
// côté serveur) : il apparaît au clic et disparaît dès la réponse, jamais
// avant, jamais après. S'il n'y a pas d'appel IA en cours, il n'existe pas.
function _cartePhaseEnCours(texte, message){
  const msg = message || tj("engine/static/engine/js/index_1.js:2607:27");
  return `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
      <span style="color:var(--text);font-weight:600;font-size:13px">${escapeHtml(texte)}</span>
      <span class="badge-en-cours">${tj("engine/static/engine/js/index_1.js:2611:37")}</span>
    </div>
    <div style="display:flex;align-items:center;color:var(--accent);font-size:13px;font-weight:500">
      <span class="spinner-ia"></span>
      <span>${escapeHtml(msg)}</span>
    </div>
    <div style="margin-top:10px;display:flex;justify-content:flex-end">
      <button class="btn-annuler-generation" style="font-size:11px;padding:3px 10px;background:transparent;border:1px solid var(--border);color:var(--danger);border-radius:5px;cursor:pointer">${tj("engine/static/engine/js/index_1.js:2618:194")}</button>
    </div>`;
}
function _cartePhaseScript(d){
  const scriptVide = !(d.script_genere || "").trim();
  const _toolbarScript = (editable) => `
    <div class="entete-fichier" style="display:flex;align-items:center;justify-content:space-between">
      <span class="nom-fichier">${tj("engine/static/engine/js/index_1.js:2625:34")}${d.id}.py</span>
      <span style="display:flex;gap:5px">
        <button class="btn-import-script-analyse" title="${tj("engine/static/engine/js/index_1.js:2627:59")}" style="font-size:11px;padding:2px 8px;background:transparent;border:1px solid var(--border);border-radius:4px;cursor:pointer;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:2627:229")}</button>
        ${editable ? "" : `<button class="btn-copier-script-analyse" title="${tj("engine/static/engine/js/index_1.js:2628:78")}" style="font-size:11px;padding:2px 8px;background:transparent;border:1px solid var(--border);border-radius:4px;cursor:pointer;color:var(--muted)">📋</button><button class="btn-sauvegarder-script-analyse" title="${tj("engine/static/engine/js/index_1.js:2628:295")}" style="font-size:11px;padding:2px 8px;background:transparent;border:1px solid var(--border);border-radius:4px;cursor:pointer;color:var(--muted)">💾</button>`}
      </span>
    </div>`;

  if(scriptVide){
    return `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
      <span style="color:var(--text);font-weight:600">${escapeHtml(d.texte)}</span>
      <span style="font-size:10px;color:var(--muted);border:1px solid var(--border);border-radius:4px;padding:1px 6px">${tj("engine/static/engine/js/index_1.js:2636:121")}</span>
    </div>
    <div class="fichier-script">
      ${_toolbarScript(true)}
      <div class="CodeMirror-editeur"><textarea class="texte-script-manuel cm-src-edition editeur-script-brut" rows="12" spellcheck="false">${escapeHtml(d.script_genere || "")}</textarea></div>
      <div class="pied-fichier" style="display:flex;gap:6px">
        <button class="btn-generer-ia-carte" style="flex:0 0 auto;padding:5px 12px;background:transparent;border:1px solid var(--border);border-radius:5px;cursor:pointer;font-size:12px;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:2642:207")}</button>
        <button class="principal btn-lancer" style="flex:1">${tj("engine/static/engine/js/index_1.js:2643:62")}</button>
      </div>
    </div>`;
  }
  return `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
      <span style="color:var(--muted);font-weight:600">${escapeHtml(d.texte)}</span>
    </div>
    <div class="fichier-script">
      ${_toolbarScript(false)}
      <div class="cm-lecture-seule CodeMirror-lecture"><textarea class="cm-src-lecture-seule editeur-script-brut" readonly>${escapeHtml(d.script_genere || "")}</textarea></div>
      <div class="pied-fichier" style="display:flex;gap:6px">
        <button class="btn-import-regen-analyse" title="${tj("engine/static/engine/js/index_1.js:2655:58")}" style="font-size:11px;padding:4px 10px;background:transparent;border:1px solid var(--border);border-radius:5px;cursor:pointer;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:2655:236")}</button>
        <button class="principal btn-lancer" style="flex:1">${tj("engine/static/engine/js/index_1.js:2656:62")}</button>
      </div>
    </div>`;
}
// Éditeur de script Python — textarea brut (sans dépendance externe).
// Les fonctions ci-dessous ne font qu'appliquer le style visuel une seule
// fois par élément ; la valeur reste directement lisible/écrivable via
// ta.value, sans instance externe à synchroniser.
function _activerCMErreur(conteneur){
  conteneur.querySelectorAll(".cm-erreur-script").forEach(ta => {
    if(ta._cmDone) return;
    ta._cmDone = true;
    ta.classList.add("editeur-script-brut");
  });
}
function _activerCMLectureSeule(conteneur){
  conteneur.querySelectorAll(".cm-src-lecture-seule, .cm-src-edition").forEach(ta => {
    if(ta._cmDone) return;
    ta._cmDone = true;
    ta.classList.add("editeur-script-brut");
    ta.setAttribute("spellcheck", "false");
    if(ta.classList.contains("cm-src-edition")){
      ta.addEventListener("keydown", ev => {
        if(ev.key === "Tab"){
          ev.preventDefault();
          const start = ta.selectionStart, end = ta.selectionEnd;
          ta.value = ta.value.slice(0, start) + "    " + ta.value.slice(end);
          ta.selectionStart = ta.selectionEnd = start + 4;
        }
      });
    }
  });
}
function _cartePhaseEchec(d){
  const msg = d.erreur || d.erreur_execution || tj("engine/static/engine/js/index_1.js:2690:50");
  const titre = d.texte || d.intention || "";
  return `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
      <span style="color:var(--muted);font-weight:600;font-size:13px">${escapeHtml(titre)}</span>
      <span style="font-size:11px;color:var(--danger);font-weight:700">${tj("engine/static/engine/js/index_1.js:2695:73")}</span>
    </div>
    <div class="bloc-erreur-script"><textarea class="cm-erreur-script editeur-script-brut" readonly spellcheck="false">${escapeHtml(msg)}</textarea></div>
    <button class="btn-insister-echec principal" title="${tj("engine/static/engine/js/index_1.js:2698:58")}" style="font-size:11px;padding:3px 10px;margin-right:6px">${tj("engine/static/engine/js/index_1.js:2989:167")}</button>
    <button class="btn-fermer-echec" style="font-size:11px;padding:3px 10px;background:transparent;border:1px solid var(--border);color:var(--muted);border-radius:5px;cursor:pointer">${tj("engine/static/engine/js/index_1.js:2699:185")}</button>`;
}
function _brancher_fermer_echec(carte, d, slug){
  const btn = carte.querySelector(".btn-fermer-echec");
  if(btn) btn.onclick = () => carte.remove();
  // « Insister » : nouveau script demandé à l'IA avec l'erreur exacte ; il reste à cliquer « Lancer ».
  const btnInsister = carte.querySelector(".btn-insister-echec");
  if(btnInsister && d) btnInsister.onclick = async () => {
    btnInsister.textContent = "…"; btnInsister.disabled = true;
    try {
      await api(`/api/demandes/${d.id}/insister/`, {method:"POST"});
      carte.innerHTML = _cartePhaseEnCours(d.texte || d.intention || tj("engine/static/engine/js/index_1.js:2710:71"), tj("engine/static/engine/js/index_1.js:2710:82"));
      const demande = await attendreFinDemande(d.id);
      if(demande.statut === "script_genere"){
        carte.innerHTML = _cartePhaseScript(demande);
        _activerCMLectureSeule(carte);
        _brancher_lancer(carte, demande, slug);
      } else {
        carte.innerHTML = _cartePhaseEchec(demande);
        _brancher_fermer_echec(carte, demande, slug);
      }
    } catch(e) { btnInsister.textContent = tj("engine/static/engine/js/index_1.js:lbl_insister"); btnInsister.disabled = false; }
  };
  requestAnimationFrame(() => _activerCMErreur(carte));
}
function creerCarteEnCours(d, slug, zone){
  const carte = document.createElement("div");
  carte.className = tj("engine/static/engine/js/index_1.js:2726:22");
  carte.dataset.demandeId = d.id;
  carte.style.borderColor = "var(--accent)";
  carte.innerHTML = _cartePhaseEnCours(d.texte);
  zone.insertBefore(carte, zone.firstChild);
  return carte;
}
function _brancher_lancer(carte, d, slug){
  const btn = carte.querySelector(".btn-lancer");
  if(!btn) return;

  // Import .py dans la carte vide (éditeur CM actif)
  const btnImport = carte.querySelector(".btn-import-script-analyse");
  if(btnImport){
    btnImport.onclick = () => {
      _inputImportScript(code => {
        // Chercher l'instance CM associée à l'éditeur manuel
        const ta = carte.querySelector(".texte-script-manuel");
        if(ta) ta.value = code;
        notifierSucces(tj("engine/static/engine/js/index_1.js:2745:25"));
      });
    };
  }

  // Import .py sur la carte avec script déjà généré (lecture seule → force un re-save)
  const btnImportRegen = carte.querySelector(".btn-import-regen-analyse");
  if(btnImportRegen){
    btnImportRegen.onclick = () => {
      _inputImportScript(async code => {
        try {
          await api(`/api/demandes/${d.id}/modifier-script/`, {method:"POST", body:JSON.stringify({code})});
          await api(`/api/demandes/${d.id}/lancer/`, {method:"POST"});
          const demande = await attendreFinDemande(d.id);
          _finaliserCarte(carte, demande, slug);
        } catch(e) {/* notifié */}
      });
    };
  }

  // Copier script (carte avec script généré)
  const btnCopier = carte.querySelector(".btn-copier-script-analyse");
  if(btnCopier){
    btnCopier.onclick = async () => {
      const ta = carte.querySelector(".cm-src-lecture-seule");
      const code = ta?.value||"";
      const ok = await copierPressePapiers(code);
      if(ok) notifierSucces(tj("engine/static/engine/js/index_1.js:2772:30")); else notifierErreur(tj("engine/static/engine/js/index_1.js:2772:69"));
    };
  }

  // Sauvegarder le script généré (fichier .py sur le serveur)
  const btnSauvegarder = carte.querySelector(".btn-sauvegarder-script-analyse");
  if(btnSauvegarder){
    btnSauvegarder.onclick = async () => {
      try {
        const r = await api(`/api/demandes/${d.id}/sauvegarder-script/`, {method:"POST"});
        notifierSucces(tj("engine/static/engine/js/index_1.js:2782:25") + r.chemin);
      } catch(e) { /* déjà notifié par api() */ }
    };
  }

  btn.onclick = async (ev) => {
    if(btn.disabled) return;
    btn.textContent = tj("engine/static/engine/js/index_1.js:2789:24"); btn.disabled = true;
    try {
      // Si la carte a un éditeur manuel, sauvegarder d'abord le script
      const taEdition = carte.querySelector(".cm-src-edition");
      if(taEdition){
        const code = taEdition.value.trim();
        if(code){
          await api(`/api/demandes/${d.id}/modifier-script/`, {method:"POST", body: JSON.stringify({code})});
        }
      }
      await api(`/api/demandes/${d.id}/lancer/`, {method:"POST"});
      const demande = await attendreFinDemande(d.id);
      _finaliserCarte(carte, demande, slug);
    } catch(err) {
      btn.textContent = tj("engine/static/engine/js/index_1.js:2803:26"); btn.disabled = false;
    }
  };
}
function _finaliserCarte(carte, d, slug){
  if(d.statut === "echec" || d.erreur_execution || d.erreur){
    carte.classList.remove("carte-en-cours");
    carte.innerHTML = _cartePhaseEchec(d);
    _brancher_fermer_echec(carte, d, slug);
    return;
  }
  // Succès : transformer la carte en carte-analyse complète
  const numSauvegarde = carte.dataset.analyseNum; // préserver le numéro si déjà assigné
  carte.classList.remove("carte", "carte-en-cours");
  const carteFinale = construireCarteAnalyse(d);
  // Copier les enfants de carteFinale dans carte (même nœud DOM, même position)
  carte._analyse = carteFinale._analyse;   // sans ça, 🎨 reconstruisait la carte depuis l'ancien résultat (rapport perdu)
  carte.className = carteFinale.className;
  carte.style.borderColor = "";
  carte.style.borderLeft = carteFinale.style.borderLeft;   // liseré de la couleur de carte (sinon perdu après Lancer/Actualiser)
  carte.innerHTML = carteFinale.innerHTML;
  // Restaurer le badge numéro si la carte avait déjà un numéro
  if(numSauvegarde){
    carte.dataset.analyseNum = numSauvegarde;
    const titreEl = carte.querySelector(".titre-analyse");
    if(titreEl && !titreEl.querySelector(".badge-analyse-num")){
      const badge = document.createElement("span");
      badge.className = "badge-analyse-num";
      badge.textContent = `#${numSauvegarde}`;
      titreEl.prepend(badge);
    }
  }
  // Rebrancher tous les event listeners (construireCarteAnalyse les attache sur carteFinale,
  // mais notre carte est un nœud différent — on reconstruit via remplacement ciblé)
  _rebrancher_carte_analyse(carte, d, slug);
}
function _rebrancher_carte_analyse(carte, a, slug){
  carte.querySelector(".btn-reduire").onclick = () => {
    const reduit = carte.classList.toggle("reduite");
    carte.querySelector(".btn-reduire").textContent = reduit ? "▸" : "▾";
  };
  carte.querySelector(".btn-supprimer-analyse").onclick = async () => {
    if(!await GY.confirmer(tj("engine/static/engine/js/index_1.js:2843:18"))) return;
    try {
      await api(`/api/demandes/${a.id}/supprimer/`, {method:"POST"});
      carte.remove();
      notifierSucces(tj("engine/static/engine/js/index_1.js:2847:23"));
      rafraichirTables();
    } catch(e) {}
  };
  carte.querySelector(".btn-voir-script").onclick = () => {
    const bloc = carte.querySelector(".bloc-script-manuel");
    const visible = bloc.style.display === "none";
    bloc.style.display = visible ? "block" : "none";
    if(visible) {
      // Activer le style + Tab→espaces à la première ouverture
      const ta = bloc.querySelector(".texte-script-manuel");
      if(ta && !ta._cmDone){
        ta._cmDone = true;
        ta.classList.add("editeur-script-brut");
        ta.addEventListener("keydown", ev => {
          if(ev.key === "Tab"){
            ev.preventDefault();
            const start = ta.selectionStart, end = ta.selectionEnd;
            ta.value = ta.value.slice(0, start) + "    " + ta.value.slice(end);
            ta.selectionStart = ta.selectionEnd = start + 4;
          }
        });
      }
    }
  };
  carte.querySelector(".btn-enregistrer-script").onclick = async (ev) => {
    const ta = carte.querySelector(".texte-script-manuel");
    const code = ta.value;
    if(!code.trim()){ notifierErreur(tj("engine/static/engine/js/index_1.js:2875:39")); return; }
    ev.target.textContent = "…"; ev.target.disabled = true;
    try {
      const res = await api(`/api/demandes/${a.id}/modifier-script/`, {method:"POST", body: JSON.stringify({code})});
      notifierSucces(tj("engine/static/engine/js/index_1.js:2879:23"));
      _finaliserCarte(carte, res.demande, slug);
    } catch(e) {
      ev.target.textContent = tj("engine/static/engine/js/index_1.js:2882:32"); ev.target.disabled = false;
    }
  };
  // Import .py
  const inputImport = carte.querySelector(".btn-import-script-final input[type=file]");
  if(inputImport){
    inputImport.onchange = () => {
      const f = inputImport.files[0]; if(!f) return;
      const r = new FileReader();
      r.onload = e => {
        const code = e.target.result;
        const ta = carte.querySelector(".texte-script-manuel");
        if(ta) ta.value = code;
        notifierSucces(tj("engine/static/engine/js/index_1.js:2895:25"));
      };
      r.readAsText(f, "utf-8");
    };
  }
  // Copier
  carte.querySelector(".btn-copier-script").onclick = async (ev) => {
    const ta = carte.querySelector(".texte-script-manuel");
    const code = ta?.value||"";
    if(!code.trim()){ notifierErreur(tj("engine/static/engine/js/index_1.js:2904:39")); return; }
    const btn = ev.currentTarget;
    const libelleInitial = btn.textContent;
    const ok = await copierPressePapiers(code);
    if(ok){
      btn.textContent = tj("engine/static/engine/js/index_1.js:2909:26");
      setTimeout(() => { btn.textContent = libelleInitial; }, 1400);
    } else {
      notifierErreur(tj("engine/static/engine/js/index_1.js:2912:23"));
    }
  };
  // Actualiser : ré-exécute le script déjà écrit — aucun appel IA, mais
  // s'exécute désormais sur un worker Celery : on attend la fin via
  // polling (bouton simplement désactivé pendant ce temps).
  carte.querySelector(".btn-actualiser").onclick = async (ev) => {
    ev.target.textContent = "↻ " + tj("engine/static/engine/js/index_1.js:actualisation"); ev.target.disabled = true;
    try {
      await api(`/api/demandes/${a.id}/actualiser/`, {method:"POST"});
      const demande = await attendreFinDemande(a.id);
      _finaliserCarte(carte, demande, slug);
    } catch(e) {
      ev.target.textContent = tj("engine/static/engine/js/index_1.js:2925:32"); ev.target.disabled = false;
    }
  };
  // Régénérer : seul bouton qui rappelle l'IA sur une analyse existante.
  // La carte affiche l'indicateur pendant que le worker Celery génère le
  // script, puis se met à jour en direct — pas de rechargement complet de
  // la page, pas de carte fantôme si ça échoue.
  carte.querySelector(".btn-regenerer").onclick = async (ev) => {
    const avertissement = a.modifie_manuellement
      ? tj("engine/static/engine/js/index_1.js:2934:10")
      : tj("engine/static/engine/js/index_1.js:2935:10");
    if(!await GY.confirmer(avertissement)) return;
    carte.innerHTML = _cartePhaseEnCours(a.intention ?? a.texte ?? "", tj("engine/static/engine/js/index_1.js:2937:56"));   // `a` peut être la réponse brute d'une action (champ « texte »)

    // Annulation possible pendant la régénération : on coupe le polling
    // et on restaure la carte d'origine (l'ancienne analyse reste en DB ;
    // le worker Celery, lui, continue en arrière-plan jusqu'à sa fin).
    const controller = new AbortController();
    let annule = false;
    const btnAnnuler = carte.querySelector(".btn-annuler-generation");
    if(btnAnnuler){
      btnAnnuler.onclick = () => {
        annule = true;
        btnAnnuler.disabled = true;
        btnAnnuler.textContent = tj("engine/static/engine/js/index_1.js:annulation");
        controller.abort();
        notifierSucces(tj("engine/static/engine/js/index_1.js:2951:25"));
        _finaliserCarte(carte, a, slug);
      };
    }

    try {
      await api(`/api/demandes/${a.id}/regenerer/`, {method:"POST", signal: controller.signal});
      const demande = await attendreFinDemande(a.id, {signal: controller.signal});
      if(demande.statut === "script_genere"){
        carte.innerHTML = _cartePhaseScript(demande);
        _activerCMLectureSeule(carte);
        _brancher_lancer(carte, demande, slug);
      } else {
        carte.innerHTML = _cartePhaseEchec(demande);
        _brancher_fermer_echec(carte, demande, slug);
      }
    } catch(e) {
      if(annule || (e && e.name === "AbortError")) return;
      // déjà notifié par api() ; on restaure la carte d'origine, jamais de carte fantôme
      _finaliserCarte(carte, a, slug);
    }
  };
  carte.querySelector(".btn-pdf-analyse").onclick = async (ev) => {
    ev.target.textContent = "…"; ev.target.disabled = true;
    try {
      await api(`/api/demandes/${a.id}/actualiser/`, {method:"POST"});
      const demandeActualisee = await attendreFinDemande(a.id);
      const pdf = await api(`/api/demandes/${a.id}/pdf/`, {method:"POST"});
      await gyTelecharger(pdf.url, tj("engine/static/engine/js/index_1.js:2979:37", {v1: a.id}), {succes: tj("engine/static/engine/js/index_1.js:2979:69")});
      _finaliserCarte(carte, demandeActualisee, slug);
    } catch(e) {
      ev.target.textContent = "📄 PDF"; ev.target.disabled = false;
    }
  };
}
// ── Création d'une carte vide (sans appel IA) ──────────────────────────────
async function creerCarte(slug){
  const zoneTexte = document.getElementById("texte-nouvelle-analyse");
  const texte = zoneTexte.value.trim();
  if(!texte){ notifierErreur(tj("engine/static/engine/js/index_1.js:2990:31")); return; }
  const bouton = document.getElementById("btn-creer-carte");
  bouton.textContent = tj("engine/static/engine/js/index_1.js:2992:25"); bouton.disabled = true;
  try {
    const res = await api("/api/demandes/creer/", {
      method: "POST",
      body: JSON.stringify({ texte, table_slug: slug }),
    });
    zoneTexte.value = "";
    const zone = document.getElementById("scripts-attente");
    // Construire directement la carte finale (même si script/résultat vide) —
    // on évite l'écran intermédiaire "Script vide" (_cartePhaseScript), qui
    // était source de bugs. La carte finale gère déjà tout : édition du
    // script, régénération IA, actualisation, suppression.
    const carte = construireCarteAnalyse(res.demande);
    zone.insertBefore(carte, zone.firstChild);
  } catch(e) { /* notifié */ } finally {
    bouton.textContent = tj("engine/static/engine/js/index_1.js:3007:27"); bouton.disabled = false;
  }
}
// Branche le bouton "Générer via IA" optionnel dans une carte dont le script est vide/manuel
function _brancher_generer_depuis_carte(carte, d, slug){
  const btn = carte.querySelector(".btn-generer-ia-carte");
  if(!btn) return;
  btn.onclick = async (ev) => {
    if(_genererEnCours) return;
    _genererEnCours = true;
    btn.textContent = tj("engine/static/engine/js/index_1.js:3017:24"); btn.disabled = true;
    try {
      await api(`/api/demandes/${d.id}/regenerer/`, { method: "POST" });
      const demande = await attendreFinDemande(d.id);
      // Remplacer le contenu de la carte par la phase script
      carte.innerHTML = _cartePhaseScript(demande);
      _activerCMLectureSeule(carte);
      _brancher_lancer(carte, demande, slug);
      _brancher_generer_depuis_carte(carte, demande, slug);
    } catch(e) { btn.textContent = tj("engine/static/engine/js/index_1.js:3026:37"); btn.disabled = false; }
    finally { _genererEnCours = false; }
  };
}
async function genererScript(slug){
  if(_genererEnCours) return;
  const zoneTexte = document.getElementById("texte-nouvelle-analyse");
  const texte = zoneTexte.value.trim();
  if(!texte) return;
  const bouton = document.getElementById("btn-generer-script");
  if(!bouton){ return; }
  _genererEnCours = true;
  bouton.textContent = tj("engine/static/engine/js/index_1.js:3038:25"); bouton.disabled = true;
  zoneTexte.value = "";

  // Le serveur délègue désormais la génération à un worker Celery et
  // répond immédiatement (statut="en_cours") : la carte "en cours" reste
  // affichée le temps du polling, jusqu'à ce que le worker ait terminé.
  const zone = document.getElementById("scripts-attente");
  const carte = creerCarteEnCours({id: "tmp", texte}, slug, zone);

  // AbortController permet à l'utilisateur d'annuler l'attente :
  // - coupe la requête HTTP initiale et/ou la boucle de polling
  // - supprime immédiatement la carte du DOM
  // Le worker Celery, lui, continue en arrière-plan jusqu'à sa fin et créera
  // bien la DemandeAgent (statut script_genere / echec). Elle réapparaîtra
  // alors dans la liste des analyses au prochain rechargement, où
  // l'utilisateur pourra la supprimer normalement.
  const controller = new AbortController();
  let annule = false;

  const btnAnnuler = carte.querySelector(".btn-annuler-generation");
  if(btnAnnuler){
    btnAnnuler.onclick = () => {
      annule = true;
      btnAnnuler.disabled = true;
      btnAnnuler.textContent = tj("engine/static/engine/js/index_1.js:annulation");
      controller.abort();
      carte.remove();
      notifierSucces(tj("engine/static/engine/js/index_1.js:3065:23"));
      _genererEnCours = false;
      bouton.textContent = tj("engine/static/engine/js/index_1.js:3067:29"); bouton.disabled = false;
    };
  }

  try{
    const res = await api("/api/demander/", {
      method:"POST",
      body: JSON.stringify({texte, table_slug: slug}),
      signal: controller.signal,
    });
    carte.dataset.demandeId = res.demande.id;
    const demande = await attendreFinDemande(res.demande.id, {signal: controller.signal});
    carte.classList.remove("carte-en-cours");
    if(demande.statut === "script_genere"){
      carte.innerHTML = _cartePhaseScript(demande);
      _activerCMLectureSeule(carte);
      _brancher_lancer(carte, demande, slug);
    } else {
      carte.innerHTML = _cartePhaseEchec(demande);
      _brancher_fermer_echec(carte, demande, slug);
    }
  } catch(e) {
    // Annulation explicite : carte déjà retirée par le handler du bouton.
    if(annule || (e && e.name === "AbortError")) return;
    // déjà notifié par api() ; on retire la carte au lieu de la laisser figée
    carte.remove();
  } finally {
    if(!annule){
      _genererEnCours = false;
      bouton.textContent = tj("engine/static/engine/js/index_1.js:3096:29"); bouton.disabled = false;
    }
  }
}
// Reprise au rechargement du dashboard : les demandes en_cours du serveur
// sont réinjectées dans #scripts-attente avec polling actif.
function afficherScriptsEnAttente(slug){
  // Conservé pour compatibilité avec le code de reprise (ligne ~883)
  // mais ne fait plus rien : les cartes vivent déjà dans le DOM.
}
const CLES_TEXTE_LIBRE = new Set([
  "conclusion", "resume", "recommandation", "recommandations",
  "titre", "pourquoi", "comment", "explication", "analyse_predictive",
]);
function titreLisible(cle){
  return cle.replace(/_/g," ").replace(/^./, c => c.toUpperCase());
}
function rendreResultat(resultat, couleurs){
  if(!resultat) return "";
  couleurs = couleurs || {};
  let html = "";
  for(const [cle, valeur] of Object.entries(resultat)){
    if(cle === "figures") continue;
    if(valeur === null || valeur === undefined || valeur === "" ||
       (Array.isArray(valeur) && valeur.length===0) ||
       (typeof valeur === "object" && !Array.isArray(valeur) && Object.keys(valeur).length===0)) continue;

    // Couleur de la section (sinon celle de la carte) : bandeau plein ; bouton 🎨 pour la choisir (POST /api/analyses/<id>/couleur/).
    const cSec = couleurs[cle] || couleurs._carte || "";
    const styleSec = cSec ? `background:${cSec};color:${_texteSurCouleur(cSec)};padding:3px 8px;border-radius:4px;` : "color:var(--accent);";
    html += `<div style="${styleSec}font-weight:600;font-size:11px;text-transform:uppercase;margin:8px 0 4px;display:flex;justify-content:space-between;align-items:center">${escapeHtml(titreLisible(cle))}<button type="button" class="btn-couleur-analyse" data-section="${escapeHtml(cle)}" title="${tj("engine/static/engine/js/index_1.js:a:3139:295")}" style="font-size:10px;padding:0 5px;border:1px solid var(--border);border-radius:3px;background:transparent;cursor:pointer">🎨</button></div>`;

    if(CLES_TEXTE_LIBRE.has(cle) && typeof valeur === "string"){
      html += `<p class="explication">${escapeHtml(valeur)}</p>`;
    } else if(Array.isArray(valeur) && valeur.length && typeof valeur[0] === "object"){
      const entetes = Object.keys(valeur[0]);
      html += '<div class="table-container"><table class="donnees"><thead><tr>' + entetes.map(e=>`<th>${escapeHtml(e)}</th>`).join("") + '</tr></thead><tbody>';
      for(const item of valeur) html += "<tr>" + entetes.map(e=>`<td>${escapeHtml(String(item[e] ?? ""))}</td>`).join("") + "</tr>";
      html += '</tbody></table></div>';
    } else if(Array.isArray(valeur)){
      html += '<ul style="margin:4px 0 4px 18px">' + valeur.map(v=>`<li>${escapeHtml(String(v))}</li>`).join("") + '</ul>';
    } else if(typeof valeur === "object"){
      html += '<div class="table-container"><table class="donnees">';
      for(const [k,v] of Object.entries(valeur)) html += `<tr><th>${escapeHtml(k)}</th><td>${escapeHtml(String(v))}</td></tr>`;
      html += '</table></div>';
    } else {
      html += `<p style="font-size:13px">${escapeHtml(String(valeur))}</p>`;
    }
  }
  return html;
}
// ══════════════════════════════════════════════════════════════
// VUE DOCUMENTS
// ══════════════════════════════════════════════════════════════
const TYPES_DOCUMENT = [
  {val:"facture","lib":"🧾 Facture"},
  {val:"recu",   "lib":tj("engine/static/engine/js/index_1.js:3152:25")},
  {val:"bon",    "lib":tj("engine/static/engine/js/index_1.js:3153:25")},
  {val:"devis",  "lib":"📝 Devis"},
  {val:"rapport","lib":"📄 Rapport"},
  {val:"autre",  "lib":"📌 Autre"},
];
async function afficherDocuments(slug){
  modeVue = "documents"; tableActive = slug; memoriserContexteAccueil();
  const table = tables.find(t => t.slug === slug);
  rendreActionsTable(table, slug);
  { const _et = document.getElementById("entete-titre"); if(_et) _et.textContent = _titreAnalyse(table); }

  const zone = document.getElementById("zone-contenu");
  zone.innerHTML = `
    <div class="zone-analyse" id="zone-creer-doc">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px">
        <div>
          <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">${tj("engine/static/engine/js/index_1.js:3169:93")}</label>
          <select id="doc-type" style="width:100%;padding:7px 10px;font-size:13px">
            ${TYPES_DOCUMENT.map(t=>`<option value="${t.val}">${t.lib}</option>`).join("")}
          </select>
        </div>
        <div>
          <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">${tj("engine/static/engine/js/index_1.js:3175:93")}</label>
          <input id="doc-titre" type="text" placeholder="ex: Facture #2025-001" style="width:100%;padding:7px 10px;font-size:13px;box-sizing:border-box">
        </div>
      </div>
      <div style="margin-bottom:8px">
        <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">
          ${tj("engine/static/engine/js/index_1.js:3181:12")} <span style="color:var(--danger)">*</span>
          <span style="font-weight:400;color:var(--muted)"> ${tj("engine/static/engine/js/index_1.js:3182:62")}</span>
        </label>
        <select id="doc-entite" style="width:100%;padding:7px 10px;font-size:13px">
          <option value="">${tj("engine/static/engine/js/index_1.js:3185:29")}</option>
        </select>
        <div id="doc-entite-resume" style="font-size:11px;color:var(--muted);margin-top:4px;min-height:16px"></div>
      </div>
      <div style="margin-bottom:8px">
        <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">${tj("engine/static/engine/js/index_1.js:3190:91")} <span style="font-weight:400">${tj("engine/static/engine/js/index_1.js:3483:172")}</span></label>
        <textarea id="doc-instructions" rows="2" placeholder="${tj("engine/static/engine/js/index_1.js:3191:64")}" style="width:100%;font-size:13px;resize:vertical"></textarea>
      </div>
      <div class="boutons-doc-actions" style="display:flex;gap:8px;margin-top:4px">
        <button id="btn-creer-doc" style="flex:1;min-width:0;padding:8px 14px;font-size:13px;background:var(--panel);border:1px solid var(--border);border-radius:6px;cursor:pointer;color:var(--text)">${tj("engine/static/engine/js/index_1.js:3194:202")}</button>
        <button class="principal" id="btn-generer-doc" style="flex:1;min-width:0">${tj("engine/static/engine/js/index_1.js:3195:84")}</button>
      </div>
    </div>
    <div id="conteneur-documents" style="width:100%;margin-top:16px"></div>
  `;

  // Charger les lignes de la table dans le sélecteur d'entité
  (async () => {
    const sel = document.getElementById("doc-entite");
    const resume = document.getElementById("doc-entite-resume");
    try {
      const res = await api(`/api/tables/${table.id}/lignes/`);
      const lignes = res.lignes || [];
      sel.innerHTML = `<option value="">${tj("engine/static/engine/js/index_1.js:3208:42")}</option>`;
      lignes.forEach(ligne => {
        const id = ligne._id;
        // Résumé : les 3 premières valeurs non nulles hors _id
        const vals = Object.entries(ligne)
          .filter(([k, v]) => k !== "_id" && v !== null && v !== "" && v !== undefined)
          .slice(0, 3)
          .map(([k, v]) => `${k}: ${v}`)
          .join("  ·  ");
        const opt = document.createElement("option");
        opt.value = id;
        opt.textContent = `#${id}  —  ${vals || tj("engine/static/engine/js/index_1.js:3219:50")}`;
        opt.dataset.resume = vals;
        sel.appendChild(opt);
      });
      if(lignes.length === 0){
        sel.innerHTML = `<option value="">${tj("engine/static/engine/js/index_1.js:3224:44")}</option>`;
      }
    } catch(e) {
      sel.innerHTML = `<option value="">${tj("engine/static/engine/js/index_1.js:3227:42")}</option>`;
    }
    sel.onchange = () => {
      const opt = sel.options[sel.selectedIndex];
      resume.textContent = opt?.dataset.resume || "";
    };
  })();

  document.getElementById("btn-creer-doc").onclick = () => _creerDocumentVide(table.id);
  document.getElementById("btn-generer-doc").onclick = () => _genererDocument(table.id);

  await _chargerDocuments(table.id);
}
// ─────────────────────────────────────────────────────────────
// TABLES DE CALCUL : liste de calculs prédéfinis (façon Excel simplifié)
// appliqués à la table source. Chaque ligne = un nom + une formule choisie
// dans la bibliothèque + une cible (colonne entière / ligne précise /
// plage de lignes). Résultat affiché en face, et ajouté au PDF d'export.
// ─────────────────────────────────────────────────────────────
async function afficherCalcul(slug){
  modeVue = "calcul"; tableActive = slug; memoriserContexteAccueil();
  const table = tables.find(t => t.slug === slug);
  rendreActionsTable(table, slug);
  { const _et = document.getElementById("entete-titre"); if(_et) _et.textContent = _titreAnalyse(table); }

  const zone = document.getElementById("zone-contenu");
  zone.innerHTML = `
    <div class="zone-analyse" id="zone-tables-calcul">
      <p style="color:var(--muted);font-size:12px;margin:0 0 10px">
        ${tj("engine/static/engine/js/index_1.js:3256:10")} ${escapeHtml(table.nom)} ${tj("engine/static/engine/js/index_1.js:3256:93")}
      </p>
      <div id="conteneur-tables-calcul"></div>
      <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;align-items:center">
        <input type="text" id="nouvelle-table-calcul-nom" placeholder="${tj("engine/static/engine/js/index_1.js:3261:73")}" style="padding:7px 10px;font-size:12px;flex:1;min-width:220px">
        <button id="btn-nouvelle-table-calcul" style="padding:7px 12px;font-size:12px">${tj("engine/static/engine/js/index_1.js:3262:89")}</button>
        <button id="btn-gerer-formules" style="padding:7px 12px;font-size:12px">${tj("engine/static/engine/js/index_1.js:3263:82")}</button>
      </div>
    </div>
  `;
  document.getElementById("btn-nouvelle-table-calcul").onclick = () => _creerTableCalcul(table);
  document.getElementById("nouvelle-table-calcul-nom").onkeydown = (e) => {
    if(e.key === "Enter") _creerTableCalcul(table);
  };
  document.getElementById("btn-gerer-formules").onclick = () => ouvrirModaleFormule(table);
  await _chargerTablesCalcul(table);
  _suivreCalculs(table);
}

// Suivi en direct de l'onglet Calculs : la cascade serveur (données modifiées ailleurs, calculs dépendants, formule
// modifiée, import de flux...) change les résultats sans action de l'utilisateur ici. Toutes les 4 s, on relit
// les résultats (lecture seule, sans toast d'erreur) et on ne redessine QUE s'ils ont changé — et jamais pendant
// qu'une saisie ou une sélection de cible est en cours, pour ne rien faire perdre.
let _calculSuiviTimer = null, _calculSuiviSignature = "";
function _signatureCalculs(tablesCalcul){
  return JSON.stringify((tablesCalcul||[]).map(tc => [tc.id, (tc.lignes||[]).map(l => [l.id, l.resultat, l.erreur, l.resultat_calcule_le])]));
}
function _suivreCalculs(table){
  clearInterval(_calculSuiviTimer);
  _calculSuiviSignature = "";
  _calculSuiviTimer = setInterval(async () => {
    if(modeVue !== "calcul" || tableActive !== table.slug || !document.getElementById("conteneur-tables-calcul")){
      clearInterval(_calculSuiviTimer); return;
    }
    if(document.hidden) return;
    const a = document.activeElement;
    if(a && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName) && a.closest("#zone-tables-calcul")) return;
    if([...document.querySelectorAll("#conteneur-tables-calcul > *")].some(c => c._cibleSelection)) return;
    try {
      const r = await fetch(`/api/tables/${table.id}/calculs/`, {headers: {"X-Requested-With": "XMLHttpRequest"}});
      if(!r.ok) return;
      const sig = _signatureCalculs((await r.json()).tables_calcul);
      if(_calculSuiviSignature === "") { _calculSuiviSignature = sig; return; }   // 1er relevé : référence
      if(sig !== _calculSuiviSignature){ _calculSuiviSignature = sig; await _chargerTablesCalcul(table, true); }
    } catch(e) { /* silencieux : réessai au prochain tour */ }
  }, 4000);
}

async function ouvrirModaleFormule(table, formuleEnEdition){
  document.getElementById("nf-nom").value = formuleEnEdition ? formuleEnEdition.nom : "";
  document.getElementById("nf-description").value = formuleEnEdition ? formuleEnEdition.description : "";
  document.getElementById("nf-categorie").value = formuleEnEdition ? formuleEnEdition.categorie : "personnalisee";
  document.getElementById("nf-cible-type").value = formuleEnEdition ? formuleEnEdition.cible_type : "colonne";
  document.getElementById("nf-types").value = formuleEnEdition ? (formuleEnEdition.types_cibles_compatibles || []).join(",") : "";
  document.getElementById("nf-code").value = formuleEnEdition ? formuleEnEdition.code : "";
  document.getElementById("nf-prompt-ia").value = "";
  document.getElementById("btn-enregistrer-formule").textContent = formuleEnEdition ? tj("engine/static/engine/js/index_1.js:3313:88") : tj("engine/static/engine/js/index_1.js:3313:117");
  const selCibleNf = document.getElementById("nf-cible-type");
  const blocTypesNf = document.getElementById("nf-bloc-types");
  selCibleNf.onchange = () => { blocTypesNf.style.display = selCibleNf.value === "table" ? "none" : "block"; };
  selCibleNf.onchange();
  await _chargerFormulesPerso(table);
  document.getElementById("modal-formule-fond").style.display = "flex";

  document.getElementById("btn-annuler-formule").onclick = () => {
    document.getElementById("modal-formule-fond").style.display = "none";
  };
  document.getElementById("btn-import-nf-code").onclick = () => {
    _inputImportScript(code => { document.getElementById("nf-code").value = code; });
  };
  document.getElementById("btn-generer-formule-ia").onclick = async (ev) => {
    const description = document.getElementById("nf-prompt-ia").value.trim();
    if(!description){ notifierErreur(tj("engine/static/engine/js/index_1.js:3329:39")); return; }
    ev.target.textContent = tj("engine/static/engine/js/index_1.js:3330:30"); ev.target.disabled = true;
    try {
      const lancement = await api(`/api/formules-calcul/generer/`, {method:"POST", body: JSON.stringify({
        description,
        cible_type: selCibleNf.value,
        table_id: table ? table.id : null,
      })});
      const generation = await attendreFinGenerationFormule(lancement.generation_id);
      if(generation.statut === "echec"){
        notifierErreur(generation.erreur || tj("engine/static/engine/js/index_1.js:3339:46"));
        return;
      }
      document.getElementById("nf-code").value = generation.code || "";
      notifierSucces(tj("engine/static/engine/js/index_1.js:3343:23"));
    } catch(e) { /* déjà notifié */ } finally {
      ev.target.textContent = tj("engine/static/engine/js/index_1.js:3345:32"); ev.target.disabled = false;
    }
  };
  document.getElementById("btn-enregistrer-formule").onclick = async (ev) => {
    const nom = document.getElementById("nf-nom").value.trim();
    const code = document.getElementById("nf-code").value;
    if(!nom){ notifierErreur(tj("engine/static/engine/js/index_1.js:3351:31")); return; }
    if(!code.includes("def formule(")){ notifierErreur(tj("engine/static/engine/js/index_1.js:3352:57")); return; }
    const corps = {
      nom,
      description: document.getElementById("nf-description").value.trim(),
      categorie: document.getElementById("nf-categorie").value,
      cible_type: document.getElementById("nf-cible-type").value,
      types_cibles_compatibles: document.getElementById("nf-types").value.trim(),
      code,
    };
    ev.target.textContent = "…"; ev.target.disabled = true;
    try {
      if(formuleEnEdition){
        await api(`/api/formules-calcul/${formuleEnEdition.id}/`, {method:"PATCH", body: JSON.stringify(corps)});
        notifierSucces(tj("engine/static/engine/js/index_1.js:3365:25"));
      } else {
        await api(`/api/formules-calcul/`, {method:"POST", body: JSON.stringify(corps)});
        notifierSucces(tj("engine/static/engine/js/index_1.js:3368:25"));
      }
      await ouvrirModaleFormule(table); // relance en mode création, données rafraîchies
      if(table) await _chargerTablesCalcul(table); // rafraîchit les sélecteurs de formule ouverts
    } catch(e) { /* déjà notifié */ } finally {
      ev.target.textContent = formuleEnEdition ? tj("engine/static/engine/js/index_1.js:3373:51") : tj("engine/static/engine/js/index_1.js:3373:80"); ev.target.disabled = false;
    }
  };
}

async function _chargerFormulesPerso(table){
  const zone = document.getElementById("liste-formules-perso");
  zone.innerHTML = `<p style="color:var(--muted);font-size:12px">${tj("engine/static/engine/js/index_1.js:3380:67")}</p>`;
  try {
    const res = await api(`/api/formules-calcul/`);
    const perso = (res.formules || []).filter(f => !f.predefinie);
    if(!perso.length){
      zone.innerHTML = `<p style="color:var(--muted);font-size:12px">${tj("engine/static/engine/js/index_1.js:3385:71")}</p>`;
      return;
    }
    zone.innerHTML = `<div style="display:flex;flex-direction:column;gap:4px">${perso.map(f => `
      <div style="display:flex;justify-content:space-between;align-items:center;padding:5px 8px;background:var(--fond-noir,#000);border:1px solid var(--border);border-radius:6px">
        <span style="font-size:12px">${escapeHtml(f.nom)} <span style="color:var(--muted);font-size:11px">(${escapeHtml(f.categorie)})</span></span>
        <span style="display:flex;gap:4px">
          <button class="btn-editer-formule-perso" data-id="${f.id}" style="padding:2px 7px;font-size:11px;background:transparent;border:1px solid var(--border);border-radius:4px;cursor:pointer">✎</button>
          <button class="btn-suppr-formule-perso" data-id="${f.id}" style="padding:2px 7px;font-size:11px;background:transparent;border:1px solid var(--border);border-radius:4px;cursor:pointer;color:var(--danger)">✕</button>
        </span>
      </div>`).join("")}</div>`;
    zone.querySelectorAll(".btn-editer-formule-perso").forEach(btn => {
      btn.onclick = () => {
        const f = perso.find(x => String(x.id) === btn.dataset.id);
        if(f) ouvrirModaleFormule(table, f);
      };
    });
    zone.querySelectorAll(".btn-suppr-formule-perso").forEach(btn => {
      btn.onclick = async () => {
        if(!await GY.confirmer(tj("engine/static/engine/js/index_1.js:3404:22"))) return;
        try {
          await api(`/api/formules-calcul/${btn.dataset.id}/`, {method:"DELETE"});
          await _chargerFormulesPerso(table);
        } catch(e) { /* déjà notifié (ex: formule utilisée par un calcul) */ }
      };
    });
  } catch(e) {
    zone.innerHTML = `<p style="color:var(--danger);font-size:12px">${tj("engine/static/engine/js/index_1.js:3412:70")}</p>`;
  }
}

async function _creerTableCalcul(table){
  const input = document.getElementById("nouvelle-table-calcul-nom");
  const nom = input.value.trim();
  if(!nom){ notifierErreur(tj("engine/static/engine/js/index_1.js:3419:29")); return; }
  try {
    await api(`/api/tables/${table.id}/calculs/`, {method:"POST", body: JSON.stringify({nom})});
    notifierSucces(tj("engine/static/engine/js/index_1.js:3422:21"));
    input.value = "";
    await _chargerTablesCalcul(table);
  } catch(e) { /* déjà notifié */ }
}

async function _chargerTablesCalcul(table, silencieux){
  const conteneur = document.getElementById("conteneur-tables-calcul");
  if(!conteneur) return;
  if(!silencieux) conteneur.innerHTML = `<p style="color:var(--muted);font-size:13px;text-align:center">${tj("engine/static/engine/js/index_1.js:3431:106")}</p>`;
  try {
    const [resCalcul, resChamps, resFormules] = await Promise.all([
      api(`/api/tables/${table.id}/calculs/`),
      Promise.resolve(table.champs || []),
      api(`/api/formules-calcul/`),
    ]);
    const tablesCalcul = resCalcul.tables_calcul || [];
    _calculSuiviSignature = _signatureCalculs(tablesCalcul);
    const champs = resChamps || [];
    const formules = resFormules.formules || [];
    if(!tablesCalcul.length){
      conteneur.innerHTML = `<p style="color:var(--muted);font-size:13px;text-align:center;margin-top:24px">${tj("engine/static/engine/js/index_1.js:3443:110")}</p>`;
      return;
    }
    conteneur.innerHTML = "";
    tablesCalcul.forEach(tc => conteneur.appendChild(_construireCarteTableCalcul(tc, table, champs, formules)));
  } catch(e) {
    conteneur.innerHTML = `<p style="color:var(--danger);font-size:13px;text-align:center">${tj("engine/static/engine/js/index_1.js:3449:93")}</p>`;
  }
}

function _rendreResultatCalcul(l){
  if(l.erreur) return `<span style="color:var(--danger)">⚠ ${escapeHtml(l.erreur.slice(0,120))}</span>`;
  const v = l.resultat;
  if(v === null || v === undefined) return "—";
  if(typeof v === "object" && !Array.isArray(v)){
    return escapeHtml(Object.entries(v).map(([k,val])=>`${k}: ${val}`).join(" · "));
  }
  if(Array.isArray(v)){
    return escapeHtml(v.length > 5 ? tj("engine/static/engine/js/index_1.js:3461:39", {v1: v.length}) : v.map(x=> typeof x==="object" ? JSON.stringify(x) : x).join(", "));
  }
  if(typeof v === "number") return escapeHtml(Number.isInteger(v) ? String(v) : v.toFixed(2));
  return escapeHtml(String(v));
}

// ── Modal Itération : sélection individuelle/multiple de calculs sources ───
function _ouvrirModalIteration(lignesDisponibles, idsDejaCoches, onValider){
  const fond = document.getElementById("modal-iteration-fond");
  const zone = document.getElementById("modal-iteration-liste");
  zone.innerHTML = lignesDisponibles.map(l => `
    <label class="mi-ligne">
      <input type="checkbox" class="mi-case" value="${l.id}" ${idsDejaCoches.includes(l.id) ? "checked" : ""}>
      <span>${escapeHtml(l.nom)}${l.resultat!=null ? ` <span style="color:var(--muted)">= ${escapeHtml(String(l.resultat))}</span>` : ""}</span>
    </label>`).join("");
  fond._lignesDisponibles = lignesDisponibles;
  fond._onValider = onValider;
  fond.style.display = "flex";
}
document.getElementById("btn-annuler-iteration").onclick = () => {
  document.getElementById("modal-iteration-fond").style.display = "none";
};
document.getElementById("btn-valider-iteration").onclick = () => {
  const fond = document.getElementById("modal-iteration-fond");
  const ids = [...fond.querySelectorAll(".mi-case:checked")].map(c => parseInt(c.value, 10));
  if(!ids.length){ notifierErreur(tj("engine/static/engine/js/index_1.js:3486:36")); return; }
  const noms = fond._lignesDisponibles.filter(l => ids.includes(l.id)).map(l => l.nom).join(", ");
  fond._onValider(ids, noms);
  fond.style.display = "none";
};

// Couleur d'une table de calcul (cadre + titre) et de chacun de ses calculs (ligne teintée) : POST /api/calculs-couleur/<table|ligne>/<id>/.
function _appliquerCouleursCalcul(carte){
  const tc = carte._tc; if(!tc) return;
  carte.style.borderLeft = tc.couleur ? `4px solid ${tc.couleur}` : "";
  const titre = carte.querySelector("span[style*='font-weight:700']");
  if(titre){ titre.style.background = tc.couleur || ""; titre.style.color = tc.couleur ? _texteSurCouleur(tc.couleur) : ""; titre.style.padding = tc.couleur ? "2px 8px" : ""; titre.style.borderRadius = tc.couleur ? "4px" : ""; }
  carte.querySelectorAll("tr[data-ligne-id]").forEach(tr => {
    const l = (tc.lignes||[]).find(x => String(x.id) === tr.dataset.ligneId);
    tr.style.background = l && l.couleur ? _fondCouleur(l.couleur, "30") : "";
    tr.style.boxShadow = l && l.couleur ? `inset 3px 0 0 ${l.couleur}` : "";
  });
}
document.addEventListener("click", async function(e){
  const btn = e.target.closest(".btn-couleur-calcul");
  if(!btn) return;
  const carte = btn.closest("[data-calcul-id]"), tc = carte && carte._tc;
  if(!tc) return;
  const obj = btn.dataset.genre === "table" ? tc : (tc.lignes||[]).find(x => String(x.id) === btn.dataset.id);
  if(!obj) return;
  const couleur = await GYCouleur.choisir({titre: tj("engine/static/engine/js/index_1.js:couleur_de", {v1: obj.nom}), valeur: obj.couleur, apercu: obj.nom});
  if(couleur === null) return;
  try {
    const r = await api(`/api/calculs-couleur/${btn.dataset.genre}/${obj.id}/`, {method:"POST", body: JSON.stringify({couleur})});
    obj.couleur = r.couleur;
    _appliquerCouleursCalcul(carte);
    notifierSucces(r.couleur ? tj("engine/static/engine/js/index_1.js:3517:33") : tj("engine/static/engine/js/index_1.js:3517:56"));
  } catch(err) { /* api() a déjà signalé l'erreur */ }
});

function _construireCarteTableCalcul(tc, table, champs, formules){
  const carte = document.createElement("div");
  carte.className = "carte";
  carte.style.cssText = "margin-bottom:14px;padding:12px";
  carte.dataset.calculId = tc.id;
  carte._tc = tc;

  const lignesHtml = (tc.lignes||[]).map(l => `
    <tr data-ligne-id="${l.id}">
      <td style="padding:5px 8px;font-size:12px">${escapeHtml(l.nom)}</td>
      <td style="padding:5px 8px;font-size:11px;color:var(--muted)">${l.programme ? "🧩 Programme" : escapeHtml(l.formule.nom)}</td>
      <td style="padding:5px 8px;font-size:11px;color:var(--muted)">${escapeHtml(_libelleCible(l))}</td>
      <td style="padding:5px 8px;font-size:12px;font-weight:600">${_rendreResultatCalcul(l)}</td>
      <td style="padding:5px 8px;text-align:right">
        ${l.programme ? `<button class="btn-modifier-programme" title="${tj("engine/static/engine/js/index_1.js:3535:73")}" style="padding:2px 6px;font-size:11px;background:transparent;border:1px solid var(--border);border-radius:4px;cursor:pointer">✎</button>` : ""}
        <button class="btn-couleur-calcul" data-genre="ligne" data-id="${l.id}" title="${tj("engine/static/engine/js/index_1.js:a:3548:87")}" style="padding:2px 6px;font-size:11px;background:transparent;border:1px solid var(--border);border-radius:4px;cursor:pointer">🎨</button>
        <button class="btn-suppr-ligne-calcul" style="padding:2px 7px;font-size:11px;background:transparent;border:1px solid var(--border);border-radius:4px;cursor:pointer;color:var(--danger)">✕</button>
      </td>
    </tr>`).join("");

  carte.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <div>
        <span style="font-weight:700;font-size:14px">🧮 ${escapeHtml(tc.nom)}</span>
        ${tc.description ? `<div style="font-size:11px;color:var(--muted)">${escapeHtml(tc.description)}</div>` : ""}
      </div>
      <div style="display:flex;gap:6px;align-items:center">
        <button class="btn-couleur-calcul" data-genre="table" data-id="${tc.id}" title="${tj("engine/static/engine/js/index_1.js:a:3560:88")}" style="padding:4px 8px;font-size:11px">🎨</button>
        <button class="btn-recalculer-calcul" style="padding:4px 8px;font-size:11px">${tj("engine/static/engine/js/index_1.js:3841:86")}</button>
        <button class="btn-suppr-table-calcul" style="padding:4px 8px;font-size:11px;color:var(--danger)">${tj("engine/static/engine/js/index_1.js:3550:108")}</button>
      </div>
    </div>
    <table style="width:100%;border-collapse:collapse" class="table-lignes-calcul">
      <thead><tr style="text-align:left;border-bottom:1px solid var(--border)">
        <th style="padding:5px 8px;font-size:11px;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:3555:72")}</th>
        <th style="padding:5px 8px;font-size:11px;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:3848:71")}</th>
        <th style="padding:5px 8px;font-size:11px;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:3849:71")}</th>
        <th style="padding:5px 8px;font-size:11px;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:3558:72")}</th>
        <th></th>
      </tr></thead>
      <tbody class="corps-lignes-calcul">${lignesHtml}</tbody>
    </table>
    <div class="zone-nouvelle-ligne-calcul" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;align-items:flex-end">
      <div>
        <label style="font-size:10px;color:var(--muted);display:block">${tj("engine/static/engine/js/index_1.js:3565:73")}</label>
        <input type="text" class="nlc-nom" placeholder="${tj("engine/static/engine/js/index_1.js:3858:57")}" style="padding:5px 8px;font-size:12px;width:130px">
      </div>
      <div>
        <label style="font-size:10px;color:var(--muted);display:block">${tj("engine/static/engine/js/index_1.js:3861:72")}</label>
        <select class="nlc-formule" style="padding:5px 8px;font-size:12px">
          ${formules.map(f=>{
            const typesF = (f.types_cibles_compatibles||[]);
            const compatibleTable = typesF.length === 0 || champs.some(c => typesF.includes(c.type_champ||c.type));
            return `<option value="${f.id}" data-cible-type="${f.cible_type}" data-types="${typesF.join(",")}"
              ${compatibleTable ? "" : "disabled"}
              ${compatibleTable ? "" : tj("engine/static/engine/js/index_1.js:3576:41")}>${compatibleTable ? "" : "🚫 "}${escapeHtml(f.nom)}</option>`;
          }).join("")}
        </select>
      </div>
      <div class="nlc-bloc-cible">
        <label style="font-size:10px;color:var(--muted);display:block">${tj("engine/static/engine/js/index_1.js:3873:72")}</label>
        <button type="button" class="nlc-btn-cible" style="padding:5px 8px;font-size:12px;min-width:170px;text-align:left;cursor:pointer">${tj("engine/static/engine/js/index_1.js:3582:140")}</button>
        <button type="button" class="nlc-btn-iteration" style="padding:5px 8px;font-size:11px;margin-top:3px" title="${tj("engine/static/engine/js/index_1.js:3583:119")}">${tj("engine/static/engine/js/index_1.js:3583:199")}</button>
        <div class="nlc-resume-cible" style="font-size:10px;color:var(--muted);margin-top:2px;max-width:220px">${tj("engine/static/engine/js/index_1.js:3584:113")}</div>
      </div>
      <button class="principal btn-ajouter-ligne-calcul" style="padding:6px 12px;font-size:12px">${tj("engine/static/engine/js/index_1.js:3586:99")}</button>
      <button type="button" class="nlc-btn-programme" style="padding:6px 10px;font-size:12px" title="${tj("engine/static/engine/js/index_1.js:3587:103")}">${tj("engine/static/engine/js/index_1.js:3879:156")}</button>
    </div>
    <div class="nlc-panneau-programme" style="display:none;margin-top:10px;padding:10px;border:1px solid var(--border);border-radius:6px">
      <div style="font-size:11px;color:var(--muted);margin-bottom:6px">${tj("engine/static/engine/js/index_1.js:3590:73")}</div>
      <div class="nlc-editeur-programme"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:8px">
        <button type="button" class="nlc-annuler-programme" style="padding:5px 10px;font-size:12px">${tj("engine/static/engine/js/index_1.js:3593:102")}</button>
        <button type="button" class="principal nlc-enregistrer-programme" style="padding:5px 10px;font-size:12px">${tj("engine/static/engine/js/index_1.js:3594:116")}</button>
      </div>
    </div>
  `;

  const selFormule = carte.querySelector(".nlc-formule");
  const btnCible = carte.querySelector(".nlc-btn-cible");
  const resumeCible = carte.querySelector(".nlc-resume-cible");
  carte._cibleSelection = null; // rempli par _ouvrirSelecteurCible → bouton "Valider la sélection"

  function _reinitialiserCible(){
    carte._cibleSelection = null;
    resumeCible.textContent = tj("engine/static/engine/js/index_1.js:3606:32");
  }
  selFormule.onchange = _reinitialiserCible; // les colonnes compatibles/le type de cible changent avec la formule
  _reinitialiserCible();

  btnCible.onclick = () => {
    const opt = selFormule.options[selFormule.selectedIndex];
    if(!opt){ notifierErreur(tj("engine/static/engine/js/index_1.js:3613:31")); return; }
    const formuleActuelle = {
      cible_type: opt.dataset.cibleType,
      types_cibles_compatibles: (opt.dataset.types || "").split(",").filter(Boolean),
    };
    _ouvrirSelecteurCible(carte, table, champs, formuleActuelle);
  };

  carte.querySelector(".nlc-btn-iteration").onclick = () => {
    if(!(tc.lignes||[]).length){ notifierErreur(tj("engine/static/engine/js/index_1.js:3622:50")); return; }
    _ouvrirModalIteration(tc.lignes, (carte._cibleSelection && carte._cibleSelection.lignes_sources_ids) || [], (ids, noms) => {
      carte._cibleSelection = {type_cible: "resultats", lignes_sources_ids: ids};
      resumeCible.textContent = tj("engine/static/engine/js/index_1.js:3625:34", {v1: noms});
    });
  };

  // Programme de formules (éditeur visuel partagé avec la table dynamique et les KPI : programme_kpi.js, moteur formules_programme.py).
  const panneau = carte.querySelector(".nlc-panneau-programme"), champNom = carte.querySelector(".nlc-nom");
  let editeur = null, ligneEditee = null;
  const ouvrirProgramme = (ligne) => {
    ligneEditee = ligne || null;
    if(ligne) champNom.value = ligne.nom;
    editeur = ProgrammeKPI.monter(carte.querySelector(".nlc-editeur-programme"), champs.map(c => ({slug: c.slug, nom: c.nom})), ligne ? ligne.programme : [], {
      bibliotheque: formules.map(f => ({id: f.id, nom: f.nom})),
      variables: (tc.lignes || []).filter(l => !ligne || l.id !== ligne.id).map(l => l.nom),
    });
    carte.querySelector(".nlc-enregistrer-programme").textContent = ligne ? tj("engine/static/engine/js/index_1.js:3639:78") : tj("engine/static/engine/js/index_1.js:3639:112");
    panneau.style.display = "block";
  };
  carte.querySelector(".nlc-btn-programme").onclick = () => ouvrirProgramme(null);
  carte.querySelector(".nlc-annuler-programme").onclick = () => { panneau.style.display = "none"; };
  carte.querySelectorAll(".btn-modifier-programme").forEach(btn => {
    btn.onclick = () => ouvrirProgramme((tc.lignes || []).find(l => String(l.id) === btn.closest("tr").dataset.ligneId));
  });
  carte.querySelector(".nlc-enregistrer-programme").onclick = async (ev) => {
    const nom = champNom.value.trim(), programme = editeur ? editeur.lire() : [];
    if(!nom){ notifierErreur(tj("engine/static/engine/js/index_1.js:3649:31")); return; }
    if(!programme.length){ notifierErreur(tj("engine/static/engine/js/index_1.js:3650:44")); return; }
    ev.target.disabled = true;
    try {
      await api(ligneEditee ? `/api/lignes-calcul/${ligneEditee.id}/` : `/api/calculs/${tc.id}/lignes/`,
        {method: ligneEditee ? "PATCH" : "POST", body: JSON.stringify({nom, programme})});
      notifierSucces(ligneEditee ? tj("engine/static/engine/js/index_1.js:3655:37") : tj("engine/static/engine/js/index_1.js:3655:60"));
      await _chargerTablesCalcul(table);
    } catch(e) { /* déjà notifié */ } finally { ev.target.disabled = false; }
  };

  _appliquerCouleursCalcul(carte);

  carte.querySelector(".btn-ajouter-ligne-calcul").onclick = async (ev) => {
    const nom = carte.querySelector(".nlc-nom").value.trim();
    if(!nom){ notifierErreur(tj("engine/static/engine/js/index_1.js:3664:31")); return; }
    if(!carte._cibleSelection){ notifierErreur(tj("engine/static/engine/js/index_1.js:3665:49")); return; }
    const c = carte._cibleSelection;
    const corps = {
      nom,
      formule_id: parseInt(selFormule.value, 10),
      type_cible: c.type_cible,
      champ_cible_id: c.champ_cible_id,
      champs_cibles_ids: c.champs_cibles_ids,
      plage_debut: c.plage_debut,
      plage_fin: c.plage_fin,
      cibles_json: c.cibles_json || null,
      lignes_sources_ids: c.lignes_sources_ids || null,
    };
    ev.target.textContent = "…"; ev.target.disabled = true;
    try {
      await api(`/api/calculs/${tc.id}/lignes/`, {method:"POST", body: JSON.stringify(corps)});
      notifierSucces(tj("engine/static/engine/js/index_1.js:3681:23"));
      await _chargerTablesCalcul(table);
    } catch(e) { /* déjà notifié */ } finally {
      ev.target.textContent = tj("engine/static/engine/js/index_1.js:3684:32"); ev.target.disabled = false;
    }
  };

  carte.querySelector(".btn-recalculer-calcul").onclick = async (ev) => {
    ev.target.textContent = "⏳…"; ev.target.disabled = true;
    try {
      await api(`/api/calculs/${tc.id}/recalculer/`, {method:"POST"});
      notifierSucces(tj("engine/static/engine/js/index_1.js:3692:23"));
      await _chargerTablesCalcul(table);
    } catch(e) { /* déjà notifié */ } finally {
      ev.target.textContent = tj("engine/static/engine/js/index_1.js:lbl_recalculer"); ev.target.disabled = false;
    }
  };

  carte.querySelector(".btn-suppr-table-calcul").onclick = async () => {
    if(!await GY.confirmer(tj("engine/static/engine/js/index_1.js:3700:18", {v1: tc.nom}))) return;
    try {
      await api(`/api/calculs/${tc.id}/`, {method:"DELETE"});
      notifierSucces(tj("engine/static/engine/js/index_1.js:3703:23"));
      await _chargerTablesCalcul(table);
    } catch(e) { /* déjà notifié */ }
  };

  carte.querySelectorAll(".btn-suppr-ligne-calcul").forEach(btn => {
    btn.onclick = async () => {
      const ligneId = btn.closest("tr").dataset.ligneId;
      try {
        await api(`/api/lignes-calcul/${ligneId}/`, {method:"DELETE"});
        await _chargerTablesCalcul(table);
      } catch(e) { /* déjà notifié */ }
    };
  });

  return carte;
}

function _libelleColonnesCible(l){
  if(l.champ_cible_nom) return l.champ_cible_nom;
  if(l.champs_cibles_noms && l.champs_cibles_noms.length){
    return l.champs_cibles_noms.length === 1
      ? l.champs_cibles_noms[0]
      : tj("engine/static/engine/js/index_1.js:3726:10", {v1: l.champs_cibles_noms.length, v2: l.champs_cibles_noms[0], v3: l.champs_cibles_noms[l.champs_cibles_noms.length-1]});
  }
  return null;
}
function _libelleCible(l){
  const colonnes = _libelleColonnesCible(l);
  if(l.programme) return (l.lignes_sources_noms || []).length ? tj("engine/static/engine/js/index_1.js:3732:66", {v1: l.lignes_sources_noms.join(", ")}) : tj("engine/static/engine/js/index_1.js:3732:116");
  if(l.type_cible === "resultats") return tj("engine/static/engine/js/index_1.js:3733:44", {v1: (l.lignes_sources_noms||[]).join(", ")});
  if(l.type_cible === "ligne") return tj("engine/static/engine/js/index_1.js:3734:40", {v1: l.plage_debut ?? 1}) + (colonnes ? ` — ${colonnes}` : "");
  if(l.type_cible === "plage") return tj("engine/static/engine/js/index_1.js:3735:40", {v1: l.plage_debut ?? 1, v2: l.plage_fin ?? "fin"}) + (colonnes ? ` — ${colonnes}` : "");
  return colonnes || tj("engine/static/engine/js/index_1.js:3736:23");
}

// ── Sélecteur de cible graphique (tables de calcul) ─────────────────────
// État de la sélection en cours (rempli à l'ouverture du modal, consommé
// ─── SÉLECTEUR DE CIBLE GRAPHIQUE ────────────────────────────────────────────
//
// Deux modes selon si la formule attend cible_type="colonne" ou "table" :
//
//   • colonne : clic simple ou glisser sur une seule colonne (plage de lignes
//     ou colonne entière). Ctrl ignoré.
//
//   • table   : trois gestes possibles, combinables par Ctrl+clic :
//       – Clic sur en-tête          → colonne entière (toutes lignes)
//       – Glisser sur cellules      → plage de lignes sur une colonne
//       – Glisser sur en-têtes      → plage de colonnes contiguës (toutes lignes)
//     Ctrl+clic / Ctrl+glisser ajoute une sélection indépendante à la liste
//     existante (sélection discontinue). Ctrl+clic sur un élément déjà
//     sélectionné le retire.
//
// L'état interne est un tableau _selCibleEtat.selections :
//   [ { colIndex: int, ligneDebut: int|null, ligneFin: int|null }, … ]
// (ligneDebut/ligneFin null = colonne entière)
//
// À la validation on construit :
//   • pour cible_type="colonne" : champ_cible_id + plage_debut/fin classiques
//   • pour cible_type="table"   : champs_cibles_ids + cibles_json
//     (liste [{champ_id, debut, fin}] pour les sélections discontinues)

let _selCibleEtat = null;
let pendingSel = null; // sélection en cours de glisser (partagée entre _brancherInteractionsSelCible et _validerPendingSel)

async function _ouvrirSelecteurCible(carte, table, champs, formule, onValiderExterne){
  const fond = document.getElementById("modal-selecteur-cible-fond");
  const grilleEl = document.getElementById("selcible-grille");
  const resumeEl = document.getElementById("selcible-resume");
  resumeEl.textContent = tj("engine/static/engine/js/index_1.js:3772:27");
  grilleEl.innerHTML = `<tr><td style="padding:20px;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:3773:74")}</td></tr>`;
  fond.style.display = "flex";

  const typesCompat = formule.types_cibles_compatibles || [];
  const slugsCompatibles = new Set(
    champs.filter(c => typesCompat.length === 0 || typesCompat.includes(c.type_champ || c.type)).map(c => c.slug)
  );
  const multiColonnesOk = formule.cible_type === "table";

  let lignes = [];
  try {
    const data = await api(`/api/tables/${table.id}/lignes/?taille=1000`);
    let tout = data.lignes || [];
    for(let p = 2; p <= ((data.pagination || {}).pages || 1); p++) tout = tout.concat((await api(`/api/tables/${table.id}/lignes/?taille=1000&page=${p}`)).lignes || []);
    lignes = tout.slice().reverse();  // ligne n°1 = la plus ancienne, comme le calcul (une seule page de 200 faussait les numéros)
  } catch(e) {
    grilleEl.innerHTML = `<tr><td style="padding:20px;color:var(--danger)">${tj("engine/static/engine/js/index_1.js:3789:77")}</td></tr>`;
    return;
  }

  _selCibleEtat = {
    carte, formule, champs, lignes, multiColonnesOk, slugsCompatibles,
    // Liste de sélections indépendantes : [{colIndex, ligneDebut, ligneFin, zone}]
    selections: [], zoneActive: "a",
    // Si fourni, remplace la logique de validation propre aux formules de
    // TableCalcul — voir btn-valider-selcible. Permet de réutiliser TEL
    // QUEL ce sélecteur ailleurs (ex: partage de ligne/cellule) sans le
    // dupliquer : reçoit (selections, champs, lignes), peut retourner
    // `false` pour signaler une sélection invalide (le modal reste ouvert).
    onValiderExterne: onValiderExterne || null,
  };

  let html = `<thead><tr><th class="selcible-entete" style="cursor:default">#</th>`;
  champs.forEach((c, ci) => {
    const compat = slugsCompatibles.has(c.slug);
    html += `<th class="selcible-entete${compat ? "" : " selcible-incompatible"}" data-col="${ci}">${escapeHtml(c.nom)}</th>`;
  });
  html += `</tr></thead><tbody>`;
  lignes.forEach((l, ri) => {
    html += `<tr><td>${ri + 1}</td>`;
    champs.forEach((c, ci) => {
      const compat = slugsCompatibles.has(c.slug);
      let v = l[c.slug];
      if(v === null || v === undefined) v = "";
      else if(typeof v === "object") v = JSON.stringify(v);
      html += `<td class="selcible-cellule${compat ? "" : " selcible-incompatible"}" data-col="${ci}" data-ligne="${ri}">${escapeHtml(String(v).slice(0, 40))}</td>`;
    });
    html += `</tr>`;
  });
  html += `</tbody>`;
  grilleEl.innerHTML = html;

  // Hint Ctrl affiché uniquement pour les formules multi-colonnes
  const hintEl = document.querySelector("#modal-selecteur-cible-fond [style*='font-size:11px']");
  if(hintEl){
    if(multiColonnesOk){
      hintEl.textContent = tj("engine/static/engine/js/index_1.js:3829:29");
    } else {
      hintEl.textContent = tj("engine/static/engine/js/index_1.js:3831:29");
    }
  }

  let zoneBar = document.getElementById("selcible-zones");
  if(zoneBar) zoneBar.remove();
  if(multiColonnesOk){
    zoneBar = document.createElement("div");
    zoneBar.id = "selcible-zones";
    zoneBar.style.cssText = "display:flex;gap:6px;margin-bottom:6px;flex-shrink:0;flex-wrap:wrap";
    grilleEl.parentElement.insertAdjacentElement("beforebegin", zoneBar);
    _redessinerBarreZones();
  }

  _brancherInteractionsSelCible();
}

// Lettres de zone disponibles, dans l'ordre — jusqu'à ZONES_MAX zones
// indépendantes par formule (au-delà, ça devient illisible à l'écran comme
// dans Excel avec des couleurs de sélection qui se ressembleraient trop).
const ZONES_LETTRES = "abcdefghij".split("");
const ZONES_MAX = ZONES_LETTRES.length;
const ZONES_COULEURS = {
  a: gyCouleur("--forme-indigo","#6d5efc"), b: gyCouleur("--texte-orange","#f59e0b"), c: gyCouleur("--forme-vert","#10b981"), d: gyCouleur("--danger","#ef4444"), e: gyCouleur("--forme-turquoise","#06b6d4"),
  f: gyCouleur("--forme-magenta","#ec4899"), g: gyCouleur("--forme-lime","#84cc16"), h: gyCouleur("--forme-orange-vif","#f97316"), i: gyCouleur("--forme-violet-vif","#8b5cf6"), j: gyCouleur("--forme-sarcelle","#14b8a6"),
};

// Reconstruit la barre de zones à partir des zones réellement utilisées
// (+ la zone active si différente) et d'un bouton « + » tant que
// ZONES_MAX n'est pas atteint — comme les onglets de couleur d'un
// classeur Excel, sans limite arbitraire à deux.
function _redessinerBarreZones(){
  const zoneBar = document.getElementById("selcible-zones");
  if(!zoneBar) return;
  const etat = _selCibleEtat;
  const zonesUtilisees = new Set(etat.selections.map(s => s.zone || "a"));
  zonesUtilisees.add(etat.zoneActive);
  const zones = ZONES_LETTRES.filter(z => zonesUtilisees.has(z));

  let html = `<span style="font-size:11px;color:var(--muted);align-self:center">${tj("engine/static/engine/js/index_1.js:4162:81")}</span>`;
  zones.forEach(z => {
    const actif = z === etat.zoneActive;
    const couleur = ZONES_COULEURS[z];
    html += `<button type="button" class="zone-btn" data-zone="${z}" style="padding:3px 10px;font-size:11px;
      ${actif ? `background:${couleur};color:var(--texte-sombre,#fff);border-color:${couleur}` : `border-color:${couleur};color:${couleur}`}">
      ${tj("engine/static/engine/js/index_1.js:4168:7", {v1: z.toUpperCase()})}</button>`;
  });
  if(zones.length < ZONES_MAX){
    html += `<button type="button" id="selcible-zone-ajouter" style="padding:3px 10px;font-size:11px;border:1px dashed var(--border);background:transparent;color:var(--muted);border-radius:5px;cursor:pointer">${tj("engine/static/engine/js/index_1.js:4171:210")}</button>`;
  }
  zoneBar.innerHTML = html;

  zoneBar.querySelectorAll(".zone-btn").forEach(btn => btn.onclick = () => {
    etat.zoneActive = btn.dataset.zone;
    _redessinerBarreZones();
  });
  const btnAjouter = document.getElementById("selcible-zone-ajouter");
  if(btnAjouter) btnAjouter.onclick = () => {
    const suivante = ZONES_LETTRES.find(z => !zonesUtilisees.has(z));
    if(!suivante) return;
    etat.zoneActive = suivante;
    _redessinerBarreZones();
  };
}

function _brancherInteractionsSelCible(){
  const grilleEl = document.getElementById("selcible-grille");
  let dragMode = null; // "colonne" | "cellule"
  let ancCol = null, ancLigne = null;
  let dragCtrl = false;
  let pointerId = null;
  const estCompatible = (el) => el && !el.classList.contains("selcible-incompatible");

  // Pointer Events unifie souris, doigt (tactile) et stylet en une seule API :
  // même geste de glisser-déposer fonctionne à l'identique sur les trois.
  // Une fois la capture posée sur grilleEl, tous les événements pointermove/up
  // lui sont routés même si le doigt quitte la cellule de départ — mais
  // e.target reste alors figé sur l'élément de départ, donc pour connaître
  // l'élément réellement survolé pendant le glisser on repasse par les
  // coordonnées (document.elementFromPoint), nécessaire sur tactile où il
  // n'existe pas d'équivalent à "mouseover" pendant un drag.
  const elementSousPointeur = (e) => {
    if(typeof e.clientX !== "number") return null;
    return document.elementFromPoint(e.clientX, e.clientY);
  };

  grilleEl.onpointerdown = (e) => {
    dragCtrl = e.ctrlKey || e.metaKey || !!(window.gyTactile && window.gyTactile.prendre("ajouter"));
    const th = e.target.closest(".selcible-entete");
    const td = e.target.closest(".selcible-cellule");
    if(th && th.dataset.col !== undefined && estCompatible(th)){
      dragMode = "colonne";
      ancCol = parseInt(th.dataset.col, 10);
      pendingSel = { colIndex: ancCol, ligneDebut: null, ligneFin: null };
      _rafraichirApercu(pendingSel);
      e.preventDefault();
    } else if(td && estCompatible(td)){
      dragMode = "cellule";
      ancCol = parseInt(td.dataset.col, 10);
      ancLigne = parseInt(td.dataset.ligne, 10);
      pendingSel = { colIndex: ancCol, ligneDebut: ancLigne, ligneFin: ancLigne };
      _rafraichirApercu(pendingSel);
      e.preventDefault();
    } else {
      return;
    }
    pointerId = e.pointerId;
    try { grilleEl.setPointerCapture(pointerId); } catch(err){ /* pointer déjà relâché : sans gravité */ }
  };

  grilleEl.onpointermove = (e) => {
    if(!dragMode || !pendingSel) return;
    if(pointerId !== null && e.pointerId !== pointerId) return;
    const elReel = elementSousPointeur(e);
    if(!elReel) return;
    const multiOk = _selCibleEtat.multiColonnesOk;
    if(dragMode === "colonne"){
      const th = elReel.closest(".selcible-entete");
      if(!th || th.dataset.col === undefined || !estCompatible(th)) return;
      let col = parseInt(th.dataset.col, 10);
      if(!multiOk) col = ancCol;
      // Glisser sur plusieurs en-têtes : sélection contiguë → on crée une
      // entrée par colonne dans la plage (toutes avec ligneDebut/Fin null)
      pendingSel = { colDebut: Math.min(ancCol, col), colFin: Math.max(ancCol, col), ligneDebut: null, ligneFin: null, multi: true };
    } else {
      const td = elReel.closest(".selcible-cellule");
      if(!td || !estCompatible(td)) return;
      let col = parseInt(td.dataset.col, 10);
      const ligne = parseInt(td.dataset.ligne, 10);
      if(!multiOk) col = ancCol;
      if(col !== ancCol){
        // Glisser sur cellules de plusieurs colonnes : traiter comme colonnes contiguës
        pendingSel = { colDebut: Math.min(ancCol, col), colFin: Math.max(ancCol, col), ligneDebut: Math.min(ancLigne, ligne), ligneFin: Math.max(ancLigne, ligne), multi: true };
      } else {
        pendingSel = { colIndex: ancCol, ligneDebut: Math.min(ancLigne, ligne), ligneFin: Math.max(ancLigne, ligne) };
      }
    }
    _rafraichirApercu(pendingSel);
    e.preventDefault();
  };

  const terminerGlisser = (e) => {
    if(pointerId !== null){
      try { grilleEl.releasePointerCapture(pointerId); } catch(err){ /* déjà relâché */ }
    }
    pointerId = null;
    if(!dragMode || !pendingSel) { dragMode = null; pendingSel = null; return; }
    // Valider pendingSel dans la liste des sélections
    _validerPendingSel(dragCtrl);
    dragMode = null; pendingSel = null;
  };

  if(window.gyTactile && _selCibleEtat.multiColonnesOk) window.gyTactile.barre(grilleEl.parentElement && grilleEl, ["ajouter"]);
  grilleEl.onpointerup = terminerGlisser;
  grilleEl.onpointercancel = terminerGlisser; // ex. appel entrant qui coupe le geste tactile
}

// Convertit pendingSel en liste de {colIndex, ligneDebut, ligneFin, zone}
function _pendingToList(pending){
  if(!pending) return [];
  const zone = _selCibleEtat ? _selCibleEtat.zoneActive : "a";
  if(pending.multi){
    const list = [];
    for(let ci = pending.colDebut; ci <= pending.colFin; ci++){
      list.push({ colIndex: ci, ligneDebut: pending.ligneDebut, ligneFin: pending.ligneFin, zone });
    }
    return list;
  }
  return [{ colIndex: pending.colIndex, ligneDebut: pending.ligneDebut, ligneFin: pending.ligneFin, zone }];
}

function _validerPendingSel(ctrlKey){
  const etat = _selCibleEtat;
  const nouveaux = _pendingToList(pendingSel);
  if(!etat.multiColonnesOk){
    // Mode colonne simple : remplace toujours
    etat.selections = nouveaux.slice(0, 1);
  } else if(ctrlKey){
    // Ctrl : ajouter ou retirer
    for(const nv of nouveaux){
      const idx = etat.selections.findIndex(s => s.colIndex === nv.colIndex
        && s.ligneDebut === nv.ligneDebut && s.ligneFin === nv.ligneFin);
      if(idx !== -1){
        etat.selections.splice(idx, 1); // retirer si déjà présent
      } else {
        etat.selections.push(nv);
      }
    }
  } else {
    // Sans Ctrl : remplace tout
    etat.selections = nouveaux;
  }
  _redessinerSelections();
  _mettreAJourResume();
  _redessinerBarreZones();
}

function _effacerColoriage(){
  const grilleEl = document.getElementById("selcible-grille");
  grilleEl.querySelectorAll(".selcible-actif").forEach(el => {
    el.classList.remove("selcible-actif");
    el.style.background = "";
    el.style.color = "";
  });
}

function _rafraichirApercu(pending){
  const etat = _selCibleEtat;
  // Effacer actif courant (hors sélections confirmées)
  _effacerColoriage();
  // Redessiner sélections confirmées
  _colorierListe(etat.selections);
  // Colorier pending en overlay
  if(pending) _colorierListe(_pendingToList(pending));
  _mettreAJourResume(pending);
}

function _redessinerSelections(){
  _effacerColoriage();
  _colorierListe(_selCibleEtat.selections);
}

// Colorie une liste de sélections avec la couleur de leur zone (zone "a" =
// couleur historique de l'accent) — jusqu'à 10 zones distinguables par
// couleur, comme des plages nommées dans Excel.
function _colorierListe(liste){
  const grilleEl = document.getElementById("selcible-grille");
  for(const s of liste){
    const ci = s.colIndex;
    const couleur = ZONES_COULEURS[s.zone || "a"] || ZONES_COULEURS.a;
    const appliquer = (el) => {
      el.classList.add("selcible-actif");
      el.style.background = couleur;
      el.style.color = gyCouleur("--texte-sombre","#fff");
    };
    const th = grilleEl.querySelector(`.selcible-entete[data-col="${ci}"]`);
    if(th) appliquer(th);
    if(s.ligneDebut !== null){
      grilleEl.querySelectorAll(`.selcible-cellule[data-col="${ci}"]`).forEach(td => {
        const l = parseInt(td.dataset.ligne, 10);
        if(l >= s.ligneDebut && l <= s.ligneFin) appliquer(td);
      });
    } else {
      grilleEl.querySelectorAll(`.selcible-cellule[data-col="${ci}"]`).forEach(td => appliquer(td));
    }
  }
}

function _mettreAJourResume(pending){
  const etat = _selCibleEtat;
  const resumeEl = document.getElementById("selcible-resume");
  const liste = pending ? [...etat.selections, ..._pendingToList(pending)] : etat.selections;
  if(!liste.length){ resumeEl.textContent = tj("engine/static/engine/js/index_1.js:4083:46"); return; }

  const zonesUtilisees = new Set(liste.map(s => s.zone || "a"));
  const parties = liste.map(s => {
    const nom = etat.champs[s.colIndex] ? etat.champs[s.colIndex].nom : tj("engine/static/engine/js/index_1.js:col_defaut", {v1: s.colIndex});
    // On n'affiche le préfixe de zone que si plus d'une zone est utilisée
    // (sinon, comme avant, la sélection unique reste sans étiquette).
    const prefixe = (etat.multiColonnesOk && zonesUtilisees.size > 1) ? `[${(s.zone || "a").toUpperCase()}] ` : "";
    if(s.ligneDebut === null) return tj("engine/static/engine/js/index_1.js:4091:39", {v1: prefixe, v2: nom});
    if(s.ligneDebut === s.ligneFin) return tj("engine/static/engine/js/index_1.js:4092:45", {v1: prefixe, v2: nom, v3: s.ligneDebut + 1});
    return `${prefixe}${nom} L${s.ligneDebut + 1}–${s.ligneFin + 1}`;
  });
  resumeEl.textContent = parties.join(" + ");
}

document.getElementById("btn-valider-selcible").onclick = () => {
  const etat = _selCibleEtat;
  if(!etat || !etat.selections.length){ notifierErreur(tj("engine/static/engine/js/index_1.js:4100:57")); return; }

  if(etat.onValiderExterne){
    const resultat = etat.onValiderExterne(etat.selections, etat.champs, etat.lignes);
    if(resultat === false) return; // sélection invalide signalée par l'appelant : on reste ouvert
    document.getElementById("modal-selecteur-cible-fond").style.display = "none";
    return;
  }

  const sels = etat.selections;

  if(etat.formule.cible_type === "colonne"){
    // Mode colonne simple : première sélection uniquement
    const s = sels[0];
    const champ = etat.champs[s.colIndex];
    const resultat = {
      type_cible: s.ligneDebut === null ? "champ" : "plage",
      champ_cible_id: champ.id,
      champs_cibles_ids: [],
      plage_debut: s.ligneDebut === null ? null : s.ligneDebut + 1,
      plage_fin: s.ligneFin === null ? null : s.ligneFin + 1,
      cibles_json: null,
    };
    etat.carte._cibleSelection = resultat;
  } else {
    // Mode table : peut être discontinu
    const simple = sels.length === 1
      && (sels[0].ligneDebut === null || sels[0].ligneDebut === sels[0].ligneFin);

    const champIds = [...new Set(sels.map(s => etat.champs[s.colIndex].id))];
    const zonesUtilisees = new Set(sels.map(s => s.zone || "a"));

    // Détecter si toutes les sélections ont la même plage de lignes
    const memePortee = sels.every(s =>
      s.ligneDebut === sels[0].ligneDebut && s.ligneFin === sels[0].ligneFin);

    let cibles_json = null;
    let type_cible = "champ";
    let plage_debut = null, plage_fin = null;

    if(zonesUtilisees.size > 1){
      // Deux zones indépendantes : toujours en cibles_json (une matrice
      // colonnes×lignes par zone), pour que le backend puisse les distinguer.
      cibles_json = sels.map(s => ({
        champ_id: etat.champs[s.colIndex].id,
        debut: s.ligneDebut === null ? null : s.ligneDebut + 1,
        fin: s.ligneFin === null ? null : s.ligneFin + 1,
        zone: s.zone || "a",
      }));
    } else if(memePortee && sels[0].ligneDebut !== null){
      // Toutes les colonnes ont la même plage → encodage classique
      type_cible = "plage";
      plage_debut = sels[0].ligneDebut + 1;
      plage_fin = sels[0].ligneFin + 1;
    } else if(!memePortee || sels.some(s => s.ligneDebut !== null)){
      // Sélections hétérogènes → cibles_json
      cibles_json = sels.map(s => ({
        champ_id: etat.champs[s.colIndex].id,
        debut: s.ligneDebut === null ? null : s.ligneDebut + 1,
        fin: s.ligneFin === null ? null : s.ligneFin + 1,
      }));
    }

    etat.carte._cibleSelection = {
      type_cible,
      champ_cible_id: null,
      champs_cibles_ids: champIds,
      plage_debut,
      plage_fin,
      cibles_json,
    };
  }

  etat.carte.querySelector(".nlc-resume-cible").textContent = document.getElementById("selcible-resume").textContent;
  document.getElementById("modal-selecteur-cible-fond").style.display = "none";
};
function _fermerSelCible(){
  document.getElementById("modal-selecteur-cible-fond").style.display = "none";
  const quand = _selCibleEtat && _selCibleEtat.surAnnulation;   // sélection abandonnée : on libère l'appelant qui attend (voir _choisirOperandes)
  if(quand){ _selCibleEtat.surAnnulation = null; quand(); }
}
document.getElementById("btn-annuler-selcible").onclick = _fermerSelCible;
document.getElementById("btn-fermer-selcible").onclick = _fermerSelCible;
document.getElementById("modal-selecteur-cible-fond").onclick = (e) => {
  if(e.target.id === "modal-selecteur-cible-fond") _fermerSelCible();
};

function _ajouterLigneParam(cle="", val=""){
  const conteneur = document.getElementById("liste-parametres-doc");
  if(!conteneur) return;
  const ligne = document.createElement("div");
  ligne.className = "ligne-param-doc";
  ligne.style.cssText = "display:flex;gap:6px;margin-bottom:4px;align-items:center";
  ligne.innerHTML = `
    <input type="text" placeholder="${tj("engine/static/engine/js/index_1.js:a:4206:clef")}" value="${escapeHtml(cle)}" style="flex:1;padding:5px 8px;font-size:12px">
    <input type="text" placeholder="${tj("engine/static/engine/js/index_1.js:a:4207:valeur")}" value="${escapeHtml(val)}" style="flex:2;padding:5px 8px;font-size:12px">
    <button style="padding:3px 8px;font-size:12px;background:transparent;border:1px solid var(--border);border-radius:4px;cursor:pointer;color:var(--danger)" title="${tj("engine/static/engine/js/index_1.js:4196:167")}">✕</button>
  `;
  ligne.querySelector("button").onclick = () => ligne.remove();
  conteneur.appendChild(ligne);
}
function _lireParametres(){
  const params = {};
  document.querySelectorAll(".ligne-param-doc").forEach(ligne => {
    const inputs = ligne.querySelectorAll("input");
    const k = inputs[0].value.trim();
    const v = inputs[1].value.trim();
    if(k) params[k] = v;
  });
  return params;
}
function _lireCorpsDocument(tableId, generer){
  const selEntite = document.getElementById("doc-entite");
  return {
    table_id:          tableId,
    enregistrement_id: selEntite ? (parseInt(selEntite.value) || null) : null,
    type_document:     document.getElementById("doc-type")?.value,
    titre:             document.getElementById("doc-titre")?.value.trim() || "",
    instructions:      document.getElementById("doc-instructions")?.value.trim() || "",
    generer:           !!generer,
  };
}
async function _creerDocumentVide(tableId){
  const btn = document.getElementById("btn-creer-doc");
  btn.textContent = tj("engine/static/engine/js/index_1.js:4224:22"); btn.disabled = true;
  try {
    const res = await api("/api/documents/creer/", { method:"POST", body: JSON.stringify(_lireCorpsDocument(tableId, false))});
    const doc = res.document;
    const conteneur = document.getElementById("conteneur-documents");
    if(conteneur){
      const carte = _construireCarteDocument(doc, tableId);
      conteneur.prepend(document.createElement("div"));
      conteneur.firstChild.replaceWith(carte);
      // Ouvrir immédiatement la carte pour que l'utilisateur puisse éditer le script
      const corps = carte.querySelector(".corps-carte-doc");
      const btnR  = carte.querySelector(".btn-reduire");
      if(corps && btnR){ corps.style.display = "block"; btnR.textContent = "▾"; }
    }
    notifierSucces(tj("engine/static/engine/js/index_1.js:4238:21"));
  } catch(e) { /* notifié */ } finally {
    btn.textContent = tj("engine/static/engine/js/index_1.js:4240:24"); btn.disabled = false;
  }
}
async function _genererDocument(tableId){
  const btn = document.getElementById("btn-generer-doc");
  btn.textContent = tj("engine/static/engine/js/index_1.js:4245:22"); btn.disabled = true;
  try {
    const res = await api("/api/documents/creer/", { method:"POST", body: JSON.stringify(_lireCorpsDocument(tableId, true))});
    let doc = res.document;
    // Insérer tout de suite la carte (statut "en_cours" : badge "⏳ En
    // cours…") pendant que le worker Celery génère puis exécute le script.
    const conteneur = document.getElementById("conteneur-documents");
    let carteWrapper = null;
    if(conteneur){
      carteWrapper = _construireCarteDocument(doc, tableId);
      conteneur.prepend(document.createElement("div")); // placeholder
      conteneur.firstChild.replaceWith(carteWrapper);
    }
    doc = await attendreFinDocument(doc.id);
    if(carteWrapper){
      _remplacerCarteDoc(carteWrapper, doc, tableId);
    }
    if(doc.statut === "execute") notifierSucces(tj("engine/static/engine/js/index_1.js:4262:50"));
    else if(doc.statut && doc.statut.startsWith("echec")) notifierErreur(tj("engine/static/engine/js/index_1.js:4263:75") + (doc.erreur || tj("engine/static/engine/js/index_1.js:4263:103")));
  } catch(e) {
    /* déjà notifié */
  } finally {
    btn.textContent = tj("engine/static/engine/js/index_1.js:4267:24"); btn.disabled = false;
  }
}
async function _chargerDocuments(tableId){
  const conteneur = document.getElementById("conteneur-documents");
  if(!conteneur) return;
  conteneur.innerHTML = `<p style="color:var(--muted);font-size:13px;text-align:center">${tj("engine/static/engine/js/index_1.js:4273:90")}</p>`;
  try {
    const res = await api(`/api/tables/${tableId}/documents/`);
    if(!res.documents.length){
      conteneur.innerHTML = `<p style="color:var(--muted);font-size:13px;text-align:center;margin-top:24px">${tj("engine/static/engine/js/index_1.js:4277:110")}</p>`;
      return;
    }
    conteneur.innerHTML = "";
    const total = res.documents.length;
    res.documents.forEach((doc, i) => {
      const num = total - i;
      const sep = document.createElement("div");
      sep.className = "analyse-groupe-header";
      sep.innerHTML = `<span class="analyse-num">${tj("engine/static/engine/js/index_1.js:4578:50", {v1: num})}</span><span class="analyse-sep-line"></span>`;
      conteneur.appendChild(sep);
      const carteWrapper = _construireCarteDocument(doc, tableId);
      conteneur.appendChild(carteWrapper);
      // Document encore en traitement sur le worker Celery (génération ou
      // exécution) : reprendre le polling pour mettre la carte à jour sans
      // attendre un rechargement manuel de la page.
      if(doc.statut === "en_cours"){
        attendreFinDocument(doc.id)
          .then(docFini => _remplacerCarteDoc(carteWrapper, docFini, tableId))
          .catch(()=>{ /* carte retirée entre-temps ou erreur réseau */ });
      }
    });
  } catch(e) {
    conteneur.innerHTML = `<p style="color:var(--danger);font-size:13px;text-align:center">${tj("engine/static/engine/js/index_1.js:4300:93")}</p>`;
  }
}
function _iconeType(type){
  return {facture:"🧾",recu:"✅",bon:"📋",devis:"📝",rapport:"📄",autre:"📌"}[type] || "📄";
}
// ── Utilitaire : input[type=file] invisible pour import .py ───────────────
function _inputImportScript(onCode){
  const inp = document.createElement("input");
  // Le fichier peut être en .py OU en .txt (ou sans extension reconnue) : on ne
  // valide/parse rien côté Python, on copie tel quel le contenu texte dans
  // l'éditeur. L'accept doit donc lister explicitement .txt, sinon certains
  // navigateurs masquent les fichiers .txt dans le sélecteur.
  inp.type = "file"; inp.accept = ".py,.txt,text/x-python,text/plain,text/*";
  inp.style.display = "none";
  inp.onchange = () => {
    const f = inp.files[0]; if(!f) return;
    const r = new FileReader();
    r.onload = e => onCode(e.target.result);
    r.readAsText(f, "utf-8");
  };
  document.body.appendChild(inp);
  inp.click();
  setTimeout(() => inp.remove(), 30000);
}
// ── Éditeur de script "document" — textarea brut (sans dépendance externe) ──
function _activerCMDocImmediat(carte, docId){
  const ta = carte.querySelector(".texte-script-document");
  if(!ta || ta._cmDone) return;
  ta._cmDone = true;
  ta.classList.add("editeur-script-brut");
  ta.addEventListener("keydown", ev => {
    if(ev.key === "Tab"){
      ev.preventDefault();
      const start = ta.selectionStart, end = ta.selectionEnd;
      ta.value = ta.value.slice(0, start) + "    " + ta.value.slice(end);
      ta.selectionStart = ta.selectionEnd = start + 4;
    }
  });
}
function _construireCarteDocument(doc, tableId){
  const wrapper = document.createElement("div");
  wrapper.dataset.docId = doc.id;

  const estOk         = doc.statut === "execute";
  const estEchecExec  = doc.statut === "echec_execution";
  const estEchecGen   = doc.statut === "echec_generation";
  const estEchec      = doc.statut === "echec" || estEchecExec || estEchecGen;
  const enCours       = doc.statut === "en_cours";
  const scriptOk      = doc.statut === "script_genere";
  const scriptVide    = !(doc.script_genere||"").trim();
  // Échec d'exécution : le script existe et peut être corrigé directement
  const scriptEditable = scriptVide || estEchecExec;

  const badgeColor = estOk ? "var(--accent)" : estEchec ? "var(--danger)" : "var(--muted)";
  const badgeLib   = {
    "execute":          tj("engine/static/engine/js/index_1.js:4356:26"),
    "echec_execution":  tj("engine/static/engine/js/index_1.js:4357:26"),
    "echec_generation": tj("engine/static/engine/js/index_1.js:4358:26"),
    "echec":            tj("engine/static/engine/js/index_1.js:4359:26"),
    "en_cours":         tj("engine/static/engine/js/index_1.js:4360:26"),
    "script_genere":    tj("engine/static/engine/js/index_1.js:4361:26"),
    "brouillon":        tj("engine/static/engine/js/index_1.js:4362:26"),
  }[doc.statut] || doc.statut;

  const cDoc = doc.couleur || "";
  let html = `
  <div class="carte carte-document" style="margin-bottom:4px${cDoc ? `;border-left:4px solid ${cDoc}` : ""}">
    <div class="entete-carte" style="display:flex;align-items:center;justify-content:space-between${cDoc ? `;background:${cDoc};color:${_texteSurCouleur(cDoc)}` : ""}">
      <span class="titre-analyse" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <span style="font-size:18px">${_iconeType(doc.type_document)}</span>
        <span style="font-weight:700">${escapeHtml(doc.titre || doc.type_libelle || doc.type_document)}</span>
        <span style="font-size:10px;font-weight:600;color:${badgeColor};background:var(--fond-info,#0a1420);border:1px solid ${badgeColor}44;border-radius:4px;padding:1px 6px">${badgeLib}</span>
        ${doc.modifie_manuellement ? tj("engine/static/engine/js/index_1.js:4372:39") : ""}
        ${doc.enregistrement_id ? `<span style="font-size:10px;color:var(--muted);border:1px solid var(--border);border-radius:4px;padding:1px 6px">${tj("engine/static/engine/js/index_1.js:4373:150")}${doc.enregistrement_id}${doc.enregistrement_resume ? ' — '+escapeHtml(doc.enregistrement_resume) : ''}</span>` : ""}
      </span>
      <span style="display:flex;align-items:center;gap:4px;flex-shrink:0">
        <button class="btn-couleur-doc" title="${tj("engine/static/engine/js/index_1.js:4669:48")}">🎨</button>
        <button class="btn-reduire" title="${tj("engine/static/engine/js/index_1.js:4375:43")}">▸</button>
      </span>
    </div>
    <div class="corps-carte-doc" style="display:none">
  `;

  // Instructions résumées
  if(doc.instructions){
    html += `<p style="font-size:12px;color:var(--muted);margin:0 0 10px;font-style:italic">"${escapeHtml(doc.instructions.slice(0,200))}${doc.instructions.length>200?"…":""}"</p>`;
  }

  // Erreur
  if(estEchec && doc.erreur){
    html += `<div class="bloc-erreur-script"><textarea class="cm-erreur-script editeur-script-brut" readonly spellcheck="false">${escapeHtml(doc.erreur)}</textarea></div>`;
  }

  // Résultat exécuté — aperçu
  if(estOk && doc.resultat_json){
    const r = doc.resultat_json;
    html += `<div class="doc-apercu">`;
    // Parties émetteur / destinataire
    if(r.emetteur || r.destinataire){
      html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">`;
      const _partie = (titre, p) => {
        if(!p) return "";
        return `<div style="font-size:12px">
          <div style="font-size:10px;color:var(--muted);font-weight:700;margin-bottom:3px;text-transform:uppercase">${titre}</div>
          ${p.nom ? `<div style="font-weight:700">${escapeHtml(p.nom)}</div>` : ""}
          ${p.adresse ? `<div style="color:var(--muted)">${escapeHtml(p.adresse)}</div>` : ""}
          ${p.contact ? `<div style="color:var(--muted)">${escapeHtml(p.contact)}</div>` : ""}
        </div>`;
      };
      html += _partie("De", r.emetteur) + _partie("À", r.destinataire);
      html += `</div>`;
    }
    // Tableau des lignes
    if(r.lignes && r.lignes.length){
      const cols = Object.keys(r.lignes[0]);
      const libsCols = {description:"Description",quantite:tj("engine/static/engine/js/index_1.js:4412:61"),prix_unitaire:tj("engine/static/engine/js/index_1.js:lbl_prix_unitaire"),montant:tj("engine/static/engine/js/index_1.js:4412:96")};
      html += `<div style="overflow-x:auto;margin-bottom:10px">
        <table class="table-donnees" style="width:100%;font-size:12px">
          <thead><tr>${cols.map(c=>`<th style="text-align:${c==="description"?"left":"right"}">${libsCols[c]||c}</th>`).join("")}</tr></thead>
          <tbody>`;
      for(const l of r.lignes){
        html += `<tr>${cols.map(c=>{
          const v = l[c]??'';
          const align = c==="description" ? "left" : "right";
          const fmt = (typeof v==="number"&&c!=="quantite") ? v.toLocaleString((window.I18N_LOCALE||"fr-FR"),{minimumFractionDigits:2}) : escapeHtml(String(v));
          return `<td style="text-align:${align}">${fmt}</td>`;
        }).join("")}</tr>`;
      }
      html += `</tbody></table></div>`;
    }
    // Totaux
    if(r.totaux){
      const libsTotaux = {sous_total:tj("engine/static/engine/js/index_1.js:lbl_sous_total"),remise:tj("engine/static/engine/js/index_1.js:lbl_remise"),tva:"TVA",total_ht:tj("engine/static/engine/js/index_1.js:4725:87"),total_ttc:tj("engine/static/engine/js/index_1.js:4725:108"),total:tj("engine/static/engine/js/index_1.js:4725:126"),acompte:tj("engine/static/engine/js/index_1.js:lbl_acompte"),reste_a_payer:tj("engine/static/engine/js/index_1.js:4429:166")};
      html += `<div style="display:flex;flex-direction:column;align-items:flex-end;gap:3px;margin-bottom:10px">`;
      for(const [k,v] of Object.entries(r.totaux)){
        const isTotal = ["total","total_ttc","reste_a_payer"].includes(k);
        const fmt = typeof v==="number" ? v.toLocaleString((window.I18N_LOCALE||"fr-FR"),{minimumFractionDigits:2}) : escapeHtml(String(v));
        html += `<div style="display:flex;gap:16px;font-size:${isTotal?"14":"12"}px;font-weight:${isTotal?"700":"400"};color:${isTotal?"var(--accent)":"var(--text)"}">
          <span>${libsTotaux[k]||k}</span><span>${fmt}</span></div>`;
      }
      html += `</div>`;
    }
    // Notes
    if(r.notes){ html += `<div style="font-size:11px;color:var(--muted);font-style:italic;border-top:1px solid var(--border);padding-top:8px;margin-bottom:6px">${escapeHtml(r.notes)}</div>`; }
    html += `</div>`; // .doc-apercu
  }

  // ── Éditeur script — toujours visible, coloration immédiate ──
  const scriptLabel = scriptVide
    ? `<span style="font-size:10px;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:4446:57")}</span>`
    : estEchecExec ? `<span style="font-size:10px;color:var(--danger)">${tj("engine/static/engine/js/index_1.js:4447:73")}</span>`
    : ``;
  html += `
  <div class="fichier-script bloc-script-manuel" style="margin-top:10px">
    <div class="entete-fichier" style="display:flex;align-items:center;justify-content:space-between">
      <span class="nom-fichier">📄 script_document_${doc.id}.py</span>
      <span style="display:flex;gap:6px;align-items:center">
        ${scriptLabel}
        <button class="btn-import-script-doc" title="${tj("engine/static/engine/js/index_1.js:4455:55")}" style="font-size:11px;padding:2px 8px;background:transparent;border:1px solid var(--border);border-radius:4px;cursor:pointer;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:4455:225")}</button>
        <button class="btn-copier-script-doc" title="${tj("engine/static/engine/js/index_1.js:4456:55")}" style="font-size:11px;padding:2px 8px;background:transparent;border:1px solid var(--border);border-radius:4px;cursor:pointer;color:var(--muted)">📋</button>
      </span>
    </div>
    <div class="CodeMirror-editeur" style="border:1px solid var(--border);border-radius:0 0 6px 6px">
      <textarea class="texte-script-document editeur-script-brut" spellcheck="false">${escapeHtml(doc.script_genere||"")}</textarea>
    </div>
  </div>`;

  // ── Actions unifiées ──
  html += `<div class="pied-fichier" style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;padding-top:8px;border-top:1px solid var(--border)">`;
  if(estOk && doc.pdf_url){
    html += `<button type="button" class="btn-telecharger-pdf-doc principal" data-url="${escapeHtml(doc.pdf_url)}" style="padding:5px 12px;font-size:12px;display:inline-flex;align-items:center;gap:5px">${tj("engine/static/engine/js/index_1.js:4467:204")}</button>`;
  }
  if(scriptEditable){
    html += `<button class="btn-generer-ia-doc" style="font-size:11px;padding:4px 10px;background:transparent;border:1px solid var(--border);border-radius:5px;cursor:pointer;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:4470:196")}</button>`;
  } else {
    html += `<button class="btn-regen-doc" style="font-size:11px;padding:4px 10px;background:transparent;border:1px solid var(--border);border-radius:5px;cursor:pointer;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:4472:191")}</button>`;
  }
  if(!scriptVide){
    html += `<button class="btn-sauvegarder-script-doc principal" style="font-size:11px;padding:4px 12px">${tj("engine/static/engine/js/index_1.js:4475:108")}</button>`;
    html += `<button class="btn-reexec-doc" style="font-size:11px;padding:4px 10px;background:transparent;border:1px solid var(--border);border-radius:5px;cursor:pointer;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:4476:192")}</button>`;
  }
  if(scriptVide){
    html += `<button class="btn-sauvegarder-script-doc principal" style="font-size:11px;padding:4px 12px">${tj("engine/static/engine/js/index_1.js:4479:108")}</button>`;
  }
  html += `<button class="btn-suppr-doc danger" style="margin-left:auto;font-size:11px;padding:4px 10px;background:transparent;border:1px solid var(--bordure-danger,#3a1a1a);border-radius:5px;cursor:pointer;color:var(--danger)">🗑</button>`;
  html += `</div>`;

  html += `</div></div>`; // corps-carte-doc + carte
  wrapper.innerHTML = html;

  const carte = wrapper.querySelector(".carte-document");

  // Couleur de la carte (même fenêtre de choix que les cartes de rapport)
  carte.querySelector(".btn-couleur-doc").onclick = async (ev) => {
    ev.stopPropagation();
    const couleur = await GYCouleur.choisir({titre: tj("engine/static/engine/js/index_1.js:4788:54"), valeur: doc.couleur || "", apercu: doc.titre || doc.type_libelle || ""});
    if(couleur === null) return;
    try {
      const r = await api(`/api/documents/${doc.id}/couleur/`, {method:"POST", body: JSON.stringify({couleur})});
      doc.couleur = r.couleur || "";
      const ouvert = corps.style.display !== "none";
      const neuve = _construireCarteDocument(doc, tableId);
      wrapper.replaceWith(neuve);
      if(ouvert){
        neuve.querySelector(".corps-carte-doc").style.display = "block";
        neuve.querySelector(".btn-reduire").textContent = "▾";
      }
    } catch(err) { GY.alerter(err.message || err); }
  };

  // Toggle réduire/ouvrir
  const btnReduce = carte.querySelector(".btn-reduire");
  const corps = carte.querySelector(".corps-carte-doc");
  btnReduce.onclick = () => {
    const ouvert = corps.style.display !== "none";
    corps.style.display = ouvert ? "none" : "block";
    btnReduce.textContent = ouvert ? "▸" : "▾";
  };

  // Ouvrir automatiquement si : erreur d'exécution (script à corriger) ou carte vide
  // if(estEchecExec || scriptVide){
  //   corps.style.display = "block";
  //   btnReduce.textContent = "▾";
  // }

  // Activer l'éditeur immédiatement (éditeur de script + bloc traceback)
  requestAnimationFrame(() => { _activerCMDocImmediat(carte, doc.id); _activerCMErreur(carte); });

  // Import fichier .py
  carte.querySelector(".btn-import-script-doc").onclick = () => {
    _inputImportScript(code => {
      const ta = carte.querySelector(".texte-script-document"); if(ta) ta.value = code;
      notifierSucces(tj("engine/static/engine/js/index_1.js:4511:23"));
    });
  };

  // Copier script
  carte.querySelector(".btn-copier-script-doc").onclick = async () => {
    const code = carte.querySelector(".texte-script-document")?.value||"";
    const ok = await copierPressePapiers(code);
    if(ok) notifierSucces(tj("engine/static/engine/js/index_1.js:4519:28")); else notifierErreur(tj("engine/static/engine/js/index_1.js:4519:67"));
  };

  // Sauvegarder + lancer
  const btnSauv = carte.querySelector(".btn-sauvegarder-script-doc");
  if(btnSauv){
    btnSauv.onclick = async () => {
      const code = carte.querySelector(".texte-script-document")?.value||"";
      btnSauv.textContent = "⏳…"; btnSauv.disabled = true;
      try {
        const res = await api(`/api/documents/${doc.id}/modifier-script/`, {method:"POST", body:JSON.stringify({code})});
        _remplacerCarteDoc(wrapper, res.document, tableId);
      } catch(e) {/* notifié */} finally { btnSauv.textContent = tj("engine/static/engine/js/index_1.js:4531:67"); btnSauv.disabled = false; }
    };
  }

  // Télécharger le PDF (fetch + blob : un lien target="_blank" vers /media/ n'ouvre rien dans la webview de l'app)
  const btnTelPdf = carte.querySelector(".btn-telecharger-pdf-doc");
  if(btnTelPdf){
    btnTelPdf.onclick = () => gyTelecharger(btnTelPdf.dataset.url, `document_${doc.id}.pdf`, {bouton: btnTelPdf});
  }

  // Générer via IA (carte vide) — génération + exécution déléguées à
  // Celery côté serveur : on attend la fin via polling.
  const btnGenererIA = carte.querySelector(".btn-generer-ia-doc");
  if(btnGenererIA){
    btnGenererIA.onclick = async (ev) => {
      ev.target.textContent = tj("engine/static/engine/js/index_1.js:4546:32"); ev.target.disabled = true;
      try {
        await api(`/api/documents/${doc.id}/regenerer/`, {method:"POST"});
        const docFini = await attendreFinDocument(doc.id);
        _remplacerCarteDoc(wrapper, docFini, tableId);
      } catch(e) {/* notifié */} finally { ev.target.textContent = tj("engine/static/engine/js/index_1.js:4551:69"); ev.target.disabled = false; }
    };
  }

  // Régénérer
  const btnRegen = carte.querySelector(".btn-regen-doc");
  if(btnRegen){
    btnRegen.onclick = async (ev) => {
      ev.target.textContent = "⏳…"; ev.target.disabled = true;
      try {
        await api(`/api/documents/${doc.id}/regenerer/`, {method:"POST"});
        const docFini = await attendreFinDocument(doc.id);
        _remplacerCarteDoc(wrapper, docFini, tableId);
      } catch(e) {/* notifié */} finally { ev.target.textContent = tj("engine/static/engine/js/index_1.js:4564:69"); ev.target.disabled = false; }
    };
  }

  // Ré-exécuter
  const btnReex = carte.querySelector(".btn-reexec-doc");
  if(btnReex){
    btnReex.onclick = async (ev) => {
      ev.target.textContent = "⏳…"; ev.target.disabled = true;
      try {
        await api(`/api/documents/${doc.id}/executer/`, {method:"POST"});
        const docFini = await attendreFinDocument(doc.id);
        _remplacerCarteDoc(wrapper, docFini, tableId);
      } catch(e) {/* notifié */} finally { ev.target.textContent = tj("engine/static/engine/js/index_1.js:4577:69"); ev.target.disabled = false; }
    };
  }

  // Supprimer
  carte.querySelector(".btn-suppr-doc").onclick = async (ev) => {
    if(!await GY.confirmer(tj("engine/static/engine/js/index_1.js:4583:18"))) return;
    ev.target.textContent = "…"; ev.target.disabled = true;
    try {
      await api(`/api/documents/${doc.id}/supprimer/`, {method:"POST"});
      wrapper.remove();
      notifierSucces(tj("engine/static/engine/js/index_1.js:4588:23"));
    } catch(e) { ev.target.textContent = "🗑"; ev.target.disabled = false; }
  };

  return wrapper;
}
function _remplacerCarteDoc(wrapper, docData, tableId){
  const nouvelleCarte = _construireCarteDocument(docData, tableId);
  wrapper.replaceWith(nouvelleCarte);
}
document.addEventListener("click", async function(e){
  const btn = e.target.closest(".btn-couleur-analyse");
  if(!btn) return;
  e.stopPropagation();
  const carte = btn.closest(".carte-analyse"), a = carte && carte._analyse;
  if(!a || !a.id) return;
  const section = btn.dataset.section || "";
  const cle = section || "_carte";
  const couleur = await GYCouleur.choisir({titre: section ? tj("engine/static/engine/js/index_1.js:4606:62", {v1: titreLisible(section)}) : tj("engine/static/engine/js/index_1.js:4606:117"), valeur: (a.couleurs || {})[cle] || "", apercu: section ? titreLisible(section) : a.intention});
  if(couleur === null) return;
  try {
    const r = await api(`/api/analyses/${a.id}/couleur/`, {method:"POST", body: JSON.stringify({couleur, section})});
    a.couleurs = r.couleurs || {};
    const ouverte = !carte.classList.contains("reduite");
    const neuve = construireCarteAnalyse(a);
    if(ouverte) neuve.classList.remove("reduite");
    const numAnalyse = carte.dataset.analyseNum;   // garder le numéro (#N) de la carte remplacée
    if(numAnalyse){
      neuve.dataset.analyseNum = numAnalyse;
      const titreNeuve = neuve.querySelector(".titre-analyse");
      if(titreNeuve && !titreNeuve.querySelector(".badge-analyse-num")){
        const badgeNum = document.createElement("span");
        badgeNum.className = "badge-analyse-num";
        badgeNum.textContent = `#${numAnalyse}`;
        titreNeuve.prepend(badgeNum);
      }
    }
    carte.replaceWith(neuve);
    notifierSucces(couleur ? tj("engine/static/engine/js/index_1.js:4615:31") : tj("engine/static/engine/js/index_1.js:4615:54"));
  } catch(err) { /* api() a déjà signalé l'erreur */ }
});
// Carte préparée d'avance par le système (statut « proposee ») : aucun script, rien n'est lancé. « Préparer l'analyse » = l'IA écrit le
// script (comme « Insister »), puis « Lancer » ; « Retirer » supprime la demande (et le système n'y revient jamais).
function construireCarteProposee(a, slug){
  const titre = a.intention || a.texte || tj("engine/static/engine/js/index_1.js:4621:44");
  const carte = document.createElement("div");
  carte.className = tj("engine/static/engine/js/index_1.js:4623:22");
  carte.dataset.demandeId = a.id;
  carte.innerHTML = `
    <div class="entete-carte"><div class="titre-analyse">${escapeHtml(titre)}</div>
      <span class="badge perime">${tj("engine/static/engine/js/index_1.js:4952:34")}</span></div>
    <div style="padding:10px 12px">
      <p style="font-size:12px;color:var(--muted);margin:0 0 10px">${tj("engine/static/engine/js/index_1.js:4629:69")}</p>
      <button class="btn-preparer-proposee principal" style="font-size:11px;padding:3px 10px;margin-right:6px">${tj("engine/static/engine/js/index_1.js:4630:113")}</button>
      <button class="btn-retirer-proposee" style="font-size:11px;padding:3px 10px;background:transparent;border:1px solid var(--border);color:var(--muted);border-radius:5px;cursor:pointer">${tj("engine/static/engine/js/index_1.js:4956:190")}</button>
    </div>`;
  const prep = carte.querySelector(".btn-preparer-proposee"), ret = carte.querySelector(".btn-retirer-proposee");
  prep.onclick = async () => {
    prep.disabled = true; prep.textContent = "…";
    try {
      await api(`/api/demandes/${a.id}/insister/`, {method:"POST"});
      carte.innerHTML = _cartePhaseEnCours(titre, tj("engine/static/engine/js/index_1.js:4638:52"));
      const demande = await attendreFinDemande(a.id);
      if(demande.statut === "script_genere"){
        carte.innerHTML = _cartePhaseScript(demande);
        _activerCMLectureSeule(carte);
        _brancher_lancer(carte, demande, slug);
      } else {
        carte.innerHTML = _cartePhaseEchec(demande);
        _brancher_fermer_echec(carte, demande, slug);
      }
    } catch(e) { prep.disabled = false; prep.textContent = tj("engine/static/engine/js/index_1.js:4648:61"); }
  };
  ret.onclick = async () => {
    ret.disabled = true; ret.textContent = "…";
    try { await api(`/api/demandes/${a.id}/supprimer/`, {method:"POST"}); carte.remove(); rafraichirTables(); }
    catch(e) { ret.disabled = false; ret.textContent = tj("engine/static/engine/js/index_1.js:lbl_retirer"); }
  };
  return carte;
}
function construireCarteAnalyse(a){
  // Tolère aussi bien la forme renvoyée par /tableau-bord/ (intention, code,
  // erreur_execution, a_jour, calcule_le, graphiques_legendes) que celle
  // renvoyée directement par les actions (texte, script_genere, erreur) —
  // évite une carte cassée quand on met à jour une carte en direct.
  a = {
    ...a,
    intention: a.intention ?? a.texte ?? "",
    code: a.code ?? a.script_genere ?? "",
    erreur_execution: a.erreur_execution ?? a.erreur ?? "",
    graphiques: a.graphiques || [],
    graphiques_legendes: a.graphiques_legendes || [],
    calcule_le: a.calcule_le ?? a.cree_le ?? null,
    a_jour: a.a_jour !== undefined ? a.a_jour : true,
  };
  const carte = document.createElement("div");
  carte.className = a.erreur_execution ? "carte-analyse" : tj("engine/static/engine/js/index_1.js:4673:61");
  carte._analyse = a;
  const cCarte = (a.couleurs && a.couleurs._carte) || "";
  if(cCarte) carte.style.borderLeft = `4px solid ${cCarte}`;
  let html = `<div class="entete-carte"${cCarte ? ` style="background:${cCarte};color:${_texteSurCouleur(cCarte)}"` : ""}>
    <div class="titre-analyse">${escapeHtml(a.intention)}</div>
    <div style="display:flex;align-items:center;gap:4px;flex-shrink:0">
      <span class="badge ${a.a_jour ? "a-jour" : "perime"}">${a.a_jour ? tj("engine/static/engine/js/index_1.js:4680:75") : tj("engine/static/engine/js/index_1.js:4680:88")}</span>
      <button class="btn-couleur-analyse" data-section="" title="${tj("engine/static/engine/js/index_1.js:4681:67")}">🎨</button>
      <button class="btn-reduire" title="${tj("engine/static/engine/js/index_1.js:4682:43")}">${a.erreur_execution ? "▾" : "▸"}</button>
      <button class="btn-supprimer-analyse" title="${tj("engine/static/engine/js/index_1.js:4683:53")}">🗑</button>
    </div>
  </div>`;
  html += `<div class="corps-carte">`;
  if(a.modifie_manuellement){
    html += `<div style="font-size:11px;color:var(--accent);margin:-4px 0 6px">${tj("engine/static/engine/js/index_1.js:4688:81")}</div>`;
  }
  if(a.erreur_execution){
    html += `<div class="bloc-erreur-script"><textarea class="cm-erreur-script editeur-script-brut" readonly spellcheck="false">${escapeHtml(a.erreur_execution)}</textarea></div>`;
  }

  html += rendreResultat(a.resultat, a.couleurs);
  for(let i=0;i<a.graphiques.length;i++){
    html += `<img class="graphique" src="data:image/png;base64,${a.graphiques[i]}" title="${escapeHtml(a.graphiques_legendes[i]||"")}">`;
  }

  html += `<div class="meta">${a.calcule_le ? tj("engine/static/engine/js/index_1.js:4699:48") + new Date(a.calcule_le).toLocaleString() : ""}</div>`;
  html += `</div>`;  /* fin .corps-carte */
  html += `<div class="actions-carte">
    <button class="btn-voir-script">${tj("engine/static/engine/js/index_1.js:5027:37")}</button>
    <button class="btn-actualiser">${tj("engine/static/engine/js/index_1.js:4703:37")}</button>
    <button class="btn-regenerer">${tj("engine/static/engine/js/index_1.js:4704:36")}</button>
    <button class="btn-pdf-analyse principal">${tj("engine/static/engine/js/index_1.js:5030:47")}</button>
  </div>`;
  html += `<div class="bloc-script-manuel" style="display:none;margin-top:10px">
    <div class="fichier-script">
      <div class="entete-fichier" style="display:flex;align-items:center;justify-content:space-between">
        <span class="nom-fichier">${tj("engine/static/engine/js/index_1.js:4710:36")}${a.id}.py</span>
        <span style="display:flex;gap:5px">
          <label class="btn-import-script-final" title="${tj("engine/static/engine/js/index_1.js:4712:58")}" style="font-size:11px;padding:2px 8px;background:transparent;border:1px solid var(--border);border-radius:4px;cursor:pointer;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:4712:228")}<input type="file" accept=".py,text/x-python,text/plain" style="display:none"></label>
          <button class="btn-copier-script" title="${tj("engine/static/engine/js/index_1.js:4713:53")}" style="font-size:11px;padding:2px 8px;background:transparent;border:1px solid var(--border);border-radius:4px;cursor:pointer;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:4713:206")}</button>
        </span>
      </div>
      <div class="CodeMirror-editeur">
        <textarea class="texte-script-manuel editeur-script-brut" spellcheck="false">${escapeHtml(a.code || "")}</textarea>
      </div>
      <div style="display:flex;gap:8px;margin-top:6px;align-items:center">
        <button class="principal btn-enregistrer-script" style="flex:1">${tj("engine/static/engine/js/index_1.js:4720:74")}</button>
      </div>
    </div>
  </div>`;
  carte.innerHTML = html;
  _rebrancher_carte_analyse(carte, a, tableActive);
  requestAnimationFrame(() => _activerCMErreur(carte));
  return carte;
}
function valeurCellule(c, v, labelResolu){
  if(c.type === "colonne") return `${escapeHtml(v === null || v === undefined ? "" : String(v))} <span class="btn-menu-colonne" title="${tj("engine/static/engine/js/index_1.js:4730:137")}" style="cursor:pointer;color:var(--muted);font-size:10px">▾</span>`;
  if(v === null || v === undefined || v === "") return "";
  if(c.type === "relation"){
    // Afficher le label résolu avec un badge discret + bouton navigation table source
    const label = labelResolu || String(v);
    const nomCible = c.relation_vers_nom || c.relation_vers || "";
    const btnNav = (c.relation_vers && nomCible)
      ? `<button type="button" class="btn-nav-table-source" data-table-slug="${escapeHtml(c.relation_vers)}" title="${tj("engine/static/engine/js/index_1.js:5062:117", {v1: escapeHtml(nomCible)})}" style="font-size:10px;padding:1px 5px;border:1px solid var(--border);border-radius:3px;background:transparent;color:var(--muted);cursor:pointer;line-height:1.5;flex-shrink:0">↗</button>`
      : "";
    return `<span style="display:inline-flex;align-items:center;gap:5px">
      <span style="font-size:9px;color:var(--muted);border:1px solid var(--border);border-radius:3px;padding:0 3px;line-height:14px" title="${tj("engine/static/engine/js/index_1.js:4740:142")}">⇢</span>
      ${escapeHtml(label)}${btnNav}
    </span>`;
  }
  if(c.type === "image") return `<span style="display:inline-flex;align-items:center;gap:6px">
    <img src="${v}" style="max-height:40px;border-radius:4px">
    <button type="button" class="btn-ouvrir-media" data-type="image" data-url="${escapeHtml(v)}" data-titre="${escapeHtml(c.nom||"Image")}" title="${tj("engine/static/engine/js/index_1.js:a:4758:147")}" style="color:var(--accent);background:transparent;text-decoration:none;font-size:12px;border:1px solid var(--border);border-radius:4px;padding:2px 7px;line-height:1.6;cursor:pointer;display:inline-flex;align-items:center;gap:3px">${tj("engine/static/engine/js/index_1.js:4746:408")}</button>
  </span>`;
  if(c.type === "fichier") return `<span style="display:inline-flex;align-items:center;gap:6px;flex-wrap:wrap"><a href="${v}" target="_blank" rel="noopener" style="color:var(--accent);text-decoration:none;border:1px solid var(--border);border-radius:4px;padding:3px 8px;font-size:12px;display:inline-flex;align-items:center;gap:4px">${tj("engine/static/engine/js/index_1.js:4748:259")}</a>${ApercuFichier.bouton(v, c.nom || tj("engine/static/engine/js/client_1.js:223:164"))}</span>`;
  if(c.type === "son") return `<span style="display:inline-flex;align-items:center;gap:6px">
    <audio controls src="${v}" style="height:32px;max-width:220px"></audio>
    <button type="button" class="btn-ouvrir-media" data-type="son" data-url="${escapeHtml(v)}" data-titre="${escapeHtml(c.nom||"Son")}" title="${tj("engine/static/engine/js/index_1.js:a:4763:143")}" style="color:var(--accent);background:transparent;text-decoration:none;font-size:12px;border:1px solid var(--border);border-radius:4px;padding:2px 7px;line-height:1.6;cursor:pointer;display:inline-flex;align-items:center;gap:3px">${tj("engine/static/engine/js/index_1.js:4751:404")}</button>
  </span>`;
  if(c.type === "video") return `<span style="display:inline-flex;align-items:center;gap:6px">
    <video controls src="${v}" style="max-height:80px;max-width:160px;border-radius:4px"></video>
    <button type="button" class="btn-ouvrir-media" data-type="video" data-url="${escapeHtml(v)}" data-titre="${escapeHtml(c.nom||tj("engine/static/engine/js/index_1.js:4755:131"))}" title="${tj("engine/static/engine/js/index_1.js:a:4767:189")}" style="color:var(--accent);background:transparent;text-decoration:none;font-size:12px;border:1px solid var(--border);border-radius:4px;padding:2px 7px;line-height:1.6;cursor:pointer;display:inline-flex;align-items:center;gap:3px">${tj("engine/static/engine/js/index_1.js:4755:408")}</button>
  </span>`;
  if(c.type === "geolocalisation"){
    const [la, lo] = String(v).match(/-?\d+(?:\.\d+)?/g) || [];
    return la && lo ? `<a href="https://www.openstreetmap.org/?mlat=${la}&mlon=${lo}#map=17/${la}/${lo}" target="_blank" rel="noopener" class="btn petit">📍 ${la}, ${lo}</a>` : escapeHtml(String(v));
  }
  if(c.type === "couleur") return `<span style="display:inline-block;width:14px;height:14px;border-radius:3px;background:${v};vertical-align:middle;margin-right:4px;border:1px solid var(--border)"></span>${v}`;
  // `switch` : en lecture on l'affiche comme un toggle visuel (cercle
  // vert/rouge) plutôt qu'une simple chaîne "True"/"False".
  if(c.type === "switch"){
    const actif = v === true || v === "1" || v === "true" || v === "vrai";
    return `<span style="display:inline-flex;align-items:center;gap:6px;font-size:12px">
      <span style="display:inline-block;width:28px;height:16px;border-radius:9px;background:${actif?gyCouleur("--fond-succes","#0a2a1a"):gyCouleur("--bordure-info","#3a5060")};position:relative;vertical-align:middle">
        <span style="position:absolute;top:2px;left:${actif?"14px":"2px"};width:12px;height:12px;border-radius:50%;background:${actif?gyCouleur("--ok","#2ecc71"):gyCouleur("--texte-leger","#555555")};transition:left .15s"></span>
      </span>
      <span style="color:${actif?"var(--ok)":"var(--muted)"}">${actif?tj("engine/static/engine/js/index_1.js:4770:72"):tj("engine/static/engine/js/index_1.js:4770:81")}</span>
    </span>`;
  }
  // `barre_progression` : la valeur est forcée dans 0..100 (cf. valeur_typee).
  if(c.type === "barre_progression"){
    const n = Math.max(0, Math.min(100, parseInt(v,10) || 0));
    return `<div style="display:flex;align-items:center;gap:6px;min-width:120px">
      <div style="flex:1;height:8px;background:var(--fond-noir,#000000);border:1px solid var(--border);border-radius:4px;overflow:hidden">
        <div style="height:100%;width:${n}%;background:var(--accent);transition:width .2s"></div>
      </div>
      <span style="font-size:11px;color:var(--muted);min-width:30px;text-align:right">${n}%</span>
    </div>`;
  }
  if(Array.isArray(v)) return v.join(", ");
  return escapeHtml(String(v));
}
function champVersInput(c){
  // Champ calculé : jamais saisi manuellement (voir ChampDynamique.formule_expression),
  // toujours recalculé automatiquement à la sauvegarde de la ligne — champ
  // désactivé, affichage seul, sa valeur actuelle (si connue) y est montrée.
  if(c.type === "formule" || c.type === "constante" || c.type === "horodatage" || c.type === "id_client"){
    const infoBulle = c.type === "formule" ? tj("engine/static/engine/js/index_1.js:4791:47") : c.type === "id_client" ? tj("engine/static/engine/js/index_1.js:4791:123") : tj("engine/static/engine/js/index_1.js:4791:196");
    return `<input type="text" id="champ_${c.slug}" disabled title="${infoBulle}" style="opacity:.65;cursor:not-allowed">`;
  }
  if(c.type === "colonne"){
    setTimeout(() => document.querySelectorAll("select.champ-colonne:not([data-charge])").forEach(_peuplerMenuColonne), 0);
    return `<select id="champ_${c.slug}" class="champ-colonne" data-table-id="${c.relation_vers_id||""}" data-colonne="${escapeHtml(c.relation_colonne_source||"")}" data-nominale="${escapeHtml(c.relation_champ_affichage||"")}" onfocus="_peuplerMenuColonne(this)"><option value="">—</option></select>`;
  }
  if(c.type === "booleen") return `<input type="checkbox" id="champ_${c.slug}" style="width:auto">`;
  // `switch` : checkbox cachée pilotée par un toggle visuel cliquable.
  // On stocke la valeur dans un checkbox standard (True/False) — c'est le
  // champVersInput qui change, la valeur reste un booléen comme booleen.
  // Les listeners sont attachés par `initialiserWidgets()` APRÈS insertion
  // dans le DOM (on évite les <script> inline qui sont piégeux).
  if(c.type === "switch"){
    return `<label class="widget-switch" style="display:inline-flex;align-items:center;gap:8px;cursor:pointer;user-select:none">
      <input type="checkbox" id="champ_${c.slug}" style="position:absolute;opacity:0;pointer-events:none;width:0;height:0">
      <span class="switch-piste" style="display:inline-block;width:38px;height:22px;border-radius:11px;background:var(--fond-info,#3a5060);position:relative;transition:background .15s">
        <span class="switch-boule" style="position:absolute;top:2px;left:2px;width:18px;height:18px;border-radius:50%;background:var(--fond-releve,#555555);transition:left .15s,background .15s"></span>
      </span>
      <span class="switch-etiquette" style="font-size:12px;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:4810:81")}</span>
    </label>`;
  }
  // `barre_progression` : range 0..100 + affichage live du %. Le label est
  // dans le label du champ-item, on rajoute juste l'input et l'indicateur.
  // Le listener `input` est attaché par `initialiserWidgets()`.
  if(c.type === "barre_progression"){
    return `<div class="widget-barre" style="display:flex;align-items:center;gap:8px">
      <input type="range" min="0" max="100" step="1" value="0" id="champ_${c.slug}" style="flex:1;padding:0;background:transparent;border:none">
      <span class="barre-pct" style="font-size:12px;color:var(--muted);min-width:36px;text-align:right">0%</span>
    </div>`;
  }
  if(c.type === "nombre" || c.type === "note") return `<input type="number" id="champ_${c.slug}">`;
  if(c.type === "decimal" || c.type === "monnaie" || c.type === "pourcentage") return `<input type="number" step="any" id="champ_${c.slug}">`;
  if(c.type === "date") return `<input type="date" id="champ_${c.slug}">`;
  if(c.type === "heure") return `<input type="time" id="champ_${c.slug}">`;
  if(c.type === "datetime") return `<input type="datetime-local" id="champ_${c.slug}">`;
  if(c.type === "duree") return `<input type="text" placeholder="01:30:00" id="champ_${c.slug}">`;
  if(c.type === "email") return `<input type="email" id="champ_${c.slug}">`;
  if(c.type === "url") return `<input type="url" id="champ_${c.slug}">`;
  if(c.type === "telephone") return `<input type="tel" id="champ_${c.slug}">`;
  if(c.type === "couleur") return `<input type="color" id="champ_${c.slug}" style="width:auto">`;
  // Champ position : zone de saisie sur toute la largeur, puis DEUX gros boutons toujours visibles (retour à la ligne, jamais rognés) pour
  // l'utilisateur non technicien : « Choisir sur la carte » (clic sur une carte ou recherche d'adresse) et « Ma position » (GPS de l'appareil).
  if(c.type === "geolocalisation") return `<div style="display:flex;flex-direction:column;gap:6px;width:100%;min-width:0"><input type="text" id="champ_${c.slug}" placeholder="${tj("engine/static/engine/js/index_1.js:geo:placeholder")}" style="width:100%;min-width:0"><div style="display:flex;gap:6px;flex-wrap:wrap"><button type="button" onclick="_choisirSurCarte('${c.slug}', ${Number(c.id) || 0})" title="${tj("engine/static/engine/js/index_1.js:geo:carte:titre")}" style="flex:1 1 140px;padding:7px 10px;font-size:13px;font-weight:600;cursor:pointer;white-space:normal">${tj("engine/static/engine/js/index_1.js:geo:carte")}</button><button type="button" onclick="_positionGPS('${c.slug}')" style="flex:1 1 140px;padding:7px 10px;font-size:13px;font-weight:600;cursor:pointer;white-space:normal">${tj("engine/static/engine/js/index_1.js:4832:318")}</button></div></div>`;
  if(c.type === "texte_long") return `<textarea id="champ_${c.slug}" rows="2"></textarea>`;
  if(c.type === "fichier" || c.type === "image" || c.type === "son" || c.type === "video"){
    const capturable = c.type === "image" || c.type === "son" || c.type === "video";
    const libelleCapture = c.type === "image" ? "📷 Photo" : c.type === "son" ? "🎙️ Micro" : tj("engine/static/engine/js/index_1.js:4836:94");
    return `<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
      <input type="file" id="champ_${c.slug}" style="flex:1;min-width:140px" ${c.type==="image" ? 'accept="image/*"' : c.type==="son" ? 'accept="audio/*"' : c.type==="video" ? 'accept="video/*"' : ""}>
      ${capturable ? `<button type="button" class="btn-capture-media" data-slug="${c.slug}" data-kind="${c.type}" onclick="_declencherCaptureMedia(this)" style="flex-shrink:0;padding:5px 10px;font-size:12px">${libelleCapture}</button>` : ""}
    </div>`;
  }
  if(c.type === "choix_unique"){
    const options = (c.options||"").split(",").map(o=>o.trim()).filter(Boolean);
    return `<select id="champ_${c.slug}"><option value="">—</option>${options.map(o=>`<option value="${o}">${o}</option>`).join("")}</select>`;
  }
  if(c.type === "choix_multiple"){
    const options = (c.options||"").split(",").map(o=>o.trim()).filter(Boolean);
    return `<select id="champ_${c.slug}" multiple size="${Math.min(4,options.length||1)}">${options.map(o=>`<option value="${o}">${o}</option>`).join("")}</select>`;
  }
  if(c.type === "relation"){
    // Bouton qui ouvre le picker modal de recherche
    const partagee = !!c.relation_bidirectionnelle;
    return `<div class="champ-relation-picker" id="champ_${c.slug}_wrapper"
         data-slug="${c.slug}"
         data-table-id="${c.relation_vers_id||""}"
         data-champ-affichage="${c.relation_champ_affichage||""}"
         data-colonne-source="${c.relation_colonne_source||""}"
         data-bidirectionnelle="${partagee ? "1" : "0"}"
         style="display:flex;flex-direction:column;gap:4px">
      <div style="display:flex;gap:6px;align-items:center">
        <input type="hidden" id="champ_${c.slug}" value="">
        <span id="champ_${c.slug}_label" style="flex:1;font-size:12px;color:var(--muted);font-style:italic;padding:6px 8px;background:var(--fond-noir,#000000);border:1px solid var(--border);border-radius:5px;min-height:32px;display:flex;align-items:center">
          ${tj("engine/static/engine/js/index_1.js:4863:12")}
        </span>
        <button type="button" class="btn-ouvrir-picker" style="flex-shrink:0;padding:5px 10px;font-size:12px"
          onclick="_ouvrirPickerRelation(this)">${tj("engine/static/engine/js/index_1.js:4866:50")}</button>
      </div>
      ${partagee ? `
      <div style="display:flex;gap:6px;align-items:center">
        <span style="font-size:11px;color:var(--muted);flex-shrink:0">${tj("engine/static/engine/js/index_1.js:4870:72")}</span>
        <input type="text" id="champ_${c.slug}_direct" data-baseline="" placeholder="${tj("engine/static/engine/js/index_1.js:a:4883:85")}"
          style="flex:1;font-size:12px;padding:5px 8px">
      </div>` : ""}
    </div>`;
  }
  if(c.type === "vecteur" || c.type === "matrice"){
    // Grille visuelle (champ_tenseur.js) : l'utilisateur ne tape que des valeurs dans des cases ; le JSON est produit en coulisses
    // dans le <textarea id="champ_${slug}"> masqué. La forme configurée sur le champ ("3,4") fixe la taille de la grille.
    return GYTenseur.html("champ_" + c.slug, c.type, c.options);
  }
  if(c.type === "json"){
    // Document JSON libre (objet ou liste), sans forme imposée — sert à
    // construire un dataset "documents" (un document par ligne) pour
    // l'entraîneur IA (ex: modèles texte/séquence). Plutôt que de forcer
    // la saisie de JSON brut, on propose un éditeur guidé clé/valeur
    // (avec possibilité d'imbriquer objets/listes), qui maintient en
    // coulisses le <textarea id="champ_${slug}"> attendu par le reste du
    // code (validation, collecte à la soumission, etc.) — voir
    // _jsonRenduLignes / _jsonSyncTextarea plus bas. Un lien permet de
    // basculer en édition JSON brute pour les utilisateurs avancés.
    return construireEditeurJson(c.slug);
  }
  return `<input type="text" id="champ_${c.slug}">`;
}
// ── Éditeur guidé pour les champs de type "json" ────────────────────────────
// Plutôt que de faire saisir du JSON brut à l'utilisateur final, on affiche
// une liste de lignes clé/valeur (comme un mini tableur), avec un sélecteur
// de type par ligne. Choisir "Objet imbriqué" ou "Liste imbriquée" ouvre un
// sous-conteneur avec son propre bouton d'ajout : la structure peut donc
// être imbriquée sur autant de niveaux que nécessaire.
// Le <textarea id="champ_${slug}"> reste la SOURCE DE VÉRITÉ pour tout le
// reste du code (validation des champs obligatoires, collecte à la
// soumission, préremplissage) : il est simplement masqué et resynchronisé
// à chaque modification. Un lien permet de basculer en édition JSON brute.
let _jsonEtats = {}; // slug -> valeur JS actuelle (objet ou liste)

function construireEditeurJson(slug, valeurInitiale){
  _jsonEtats[slug] = (valeurInitiale && typeof valeurInitiale === "object") ? valeurInitiale : {};
  return `<div class="editeur-json" data-slug="${slug}">
    <div class="editeur-json-guide">
      <div class="editeur-json-lignes" id="champ_${slug}_lignes">${_jsonRenduLignes(slug, _jsonEtats[slug], [])}</div>
      <button type="button" class="btn-json-ajouter" onclick="_jsonAjouterEntree('${slug}', [])">${tj("engine/static/engine/js/index_1.js:4917:99")}</button>
      <div style="margin-top:6px">
        <a href="#" class="lien-json-brut" onclick="_jsonBasculerMode('${slug}');return false;"
           style="font-size:11px;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:4920:55")}</a>
      </div>
    </div>
    <textarea id="champ_${slug}" rows="4" style="display:none;font-family:ui-monospace,monospace;font-size:12px"
      title="${tj("engine/static/engine/js/index_1.js:5251:14")}">${escapeHtml(JSON.stringify(_jsonEtats[slug], null, 2))}</textarea>
  </div>`;
}
function _jsonLibelleType(t){
  return { texte:tj("engine/static/engine/js/index_1.js:4928:19"), nombre:tj("engine/static/engine/js/index_1.js:4928:35"), booleen:tj("engine/static/engine/js/index_1.js:4928:53"),
    objet:tj("engine/static/engine/js/index_1.js:4929:12"), liste:tj("engine/static/engine/js/index_1.js:4929:36"), vide:tj("engine/static/engine/js/index_1.js:4929:60") }[t] || t;
}
function _jsonTypeDe(valeur){
  if(valeur === null || valeur === undefined) return "vide";
  if(Array.isArray(valeur)) return "liste";
  if(typeof valeur === "object") return "objet";
  if(typeof valeur === "number") return "nombre";
  if(typeof valeur === "boolean") return "booleen";
  return "texte";
}
// Encode une valeur JS (chaîne, tableau de chemin...) pour l'injecter comme
// littéral JS dans un attribut HTML délimité par des apostrophes.
function _jsonAttrJs(valeur){
  return JSON.stringify(valeur).replace(/'/g, "&#39;");
}
function _jsonRenduLignes(slug, conteneur, chemin){
  const estListe = Array.isArray(conteneur);
  const entrees = estListe ? conteneur.map((v,i)=>[i,v]) : Object.entries(conteneur);
  if(entrees.length === 0){
    return `<div class="editeur-json-vide" style="font-size:11px;color:var(--muted);font-style:italic;padding:4px 0">
      ${tj("engine/static/engine/js/index_1.js:4949:8")} ${estListe ? tj("engine/static/engine/js/index_1.js:entree") : tj("engine/static/engine/js/index_1.js:cle")} ${tj("engine/static/engine/js/index_1.js:4949:46")}</div>`;
  }
  return entrees.map(([cle, valeur]) => _jsonRenduLigne(slug, chemin, cle, valeur, estListe)).join("");
}
function _jsonRenduLigne(slug, chemin, cle, valeur, estListe){
  const cheminActuel = [...chemin, cle];
  const cheminAttr = _jsonAttrJs(chemin); // chemin du CONTENEUR (pour renommer/supprimer/changer type)
  const cheminActuelAttr = _jsonAttrJs(cheminActuel); // chemin de CETTE entrée (pour éditer sa valeur)
  const cleAttr = _jsonAttrJs(cle);
  const type = _jsonTypeDe(valeur);
  const entete = estListe
    ? `<span class="json-index" style="font-size:11px;color:var(--muted);min-width:26px;flex-shrink:0">#${Number(cle)+1}</span>`
    : `<input type="text" class="json-cle champ-formulaire" value="${escapeHtml(String(cle))}" placeholder="${tj("engine/static/engine/js/index_1.js:a:4973:clef")}"
         data-cle-originale="${escapeHtml(String(cle))}" style="flex:1;min-width:70px"
         onchange="_jsonRenommerCle('${slug}', ${cheminAttr}, this.dataset.cleOriginale, this.value)">`;
  const selecteurType = `<select class="json-type champ-formulaire" style="flex-shrink:0;width:auto;font-size:11px"
      onchange="_jsonChangerType('${slug}', ${cheminAttr}, ${cleAttr}, this.value)">
    ${["texte","nombre","booleen","objet","liste","vide"].map(t =>
      `<option value="${t}" ${t===type?"selected":""}>${_jsonLibelleType(t)}</option>`).join("")}
  </select>`;
  let corpsValeur;
  if(type === "objet" || type === "liste"){
    corpsValeur = `<div class="editeur-json-imbrique" style="margin-top:6px;padding-left:14px;border-left:2px solid var(--border)">
      ${_jsonRenduLignes(slug, valeur, cheminActuel)}
      <button type="button" class="btn-json-ajouter" onclick="_jsonAjouterEntree('${slug}', ${cheminActuelAttr})">
        ${tj("engine/static/engine/js/index_1.js:4974:10")} ${type === "liste" ? tj("engine/static/engine/js/index_1.js:4974:41") : tj("engine/static/engine/js/index_1.js:4974:56")}
      </button>
    </div>`;
  } else if(type === "booleen"){
    corpsValeur = `<input type="checkbox" style="width:auto" ${valeur ? "checked" : ""}
      onchange="_jsonChangerValeurPrimitive('${slug}', ${cheminActuelAttr}, this.checked, 'booleen')">`;
  } else if(type === "vide"){
    corpsValeur = `<span style="font-size:12px;color:var(--muted)">—</span>`;
  } else {
    const val = (valeur === null || valeur === undefined) ? "" : valeur;
    corpsValeur = `<input type="${type === "nombre" ? "number" : "text"}" class="champ-formulaire" ${type === "nombre" ? 'step="any"' : ""}
      value="${escapeHtml(String(val))}" placeholder="${tj("engine/static/engine/js/index_1.js:a:4997:valeur")}"
      oninput="_jsonChangerValeurPrimitive('${slug}', ${cheminActuelAttr}, this.value, '${type}')">`;
  }
  return `<div class="ligne-json" style="display:flex;flex-direction:column;gap:4px;padding:5px 0;border-bottom:1px solid var(--border)">
    <div style="display:flex;gap:6px;align-items:center">
      ${entete}
      ${selecteurType}
      <button type="button" title="${tj("engine/static/engine/js/index_1.js:a:5004:35")}" onclick="_jsonSupprimerEntree('${slug}', ${cheminAttr}, ${cleAttr})"
        style="flex-shrink:0;padding:3px 8px;font-size:12px">✕</button>
    </div>
    <div>${corpsValeur}</div>
  </div>`;
}
function _jsonObtenirRef(slug, chemin){
  let ref = _jsonEtats[slug];
  for(const seg of chemin) ref = ref[seg];
  return ref;
}
function _jsonSyncTextarea(slug){
  const ta = document.getElementById("champ_"+slug);
  if(ta) ta.value = JSON.stringify(_jsonEtats[slug], null, 2);
}
function _jsonRerendre(slug){
  const zone = document.getElementById("champ_"+slug+"_lignes");
  if(zone) zone.innerHTML = _jsonRenduLignes(slug, _jsonEtats[slug], []);
  _jsonSyncTextarea(slug);
}
function _jsonAjouterEntree(slug, chemin){
  const conteneur = _jsonObtenirRef(slug, chemin);
  if(Array.isArray(conteneur)){
    conteneur.push("");
  } else {
    let n = 1, cle = "cle";
    while(Object.prototype.hasOwnProperty.call(conteneur, cle)) cle = "cle" + (++n);
    conteneur[cle] = "";
  }
  _jsonRerendre(slug);
}
function _jsonSupprimerEntree(slug, chemin, cle){
  const conteneur = _jsonObtenirRef(slug, chemin);
  if(Array.isArray(conteneur)) conteneur.splice(Number(cle), 1);
  else delete conteneur[cle];
  _jsonRerendre(slug);
}
function _jsonRenommerCle(slug, chemin, ancienneCle, nouvelleCle){
  nouvelleCle = (nouvelleCle || "").trim();
  const conteneur = _jsonObtenirRef(slug, chemin);
  if(Array.isArray(conteneur) || !nouvelleCle || nouvelleCle === ancienneCle){ _jsonRerendre(slug); return; }
  if(Object.prototype.hasOwnProperty.call(conteneur, nouvelleCle)){
    notifierErreur(tj("engine/static/engine/js/index_1.js:5034:21"));
    _jsonRerendre(slug);
    return;
  }
  const nouveau = {};
  for(const [k, v] of Object.entries(conteneur)) nouveau[k === ancienneCle ? nouvelleCle : k] = v;
  for(const k of Object.keys(conteneur)) delete conteneur[k];
  Object.assign(conteneur, nouveau);
  _jsonRerendre(slug);
}
function _jsonChangerType(slug, chemin, cle, nouveauType){
  const conteneur = _jsonObtenirRef(slug, chemin);
  const defauts = { texte:"", nombre:0, booleen:false, objet:{}, liste:[], vide:null };
  conteneur[cle] = defauts[nouveauType];
  _jsonRerendre(slug);
}
function _jsonChangerValeurPrimitive(slug, cheminEntree, valeur, type){
  const conteneur = _jsonObtenirRef(slug, cheminEntree.slice(0, -1));
  const cle = cheminEntree[cheminEntree.length - 1];
  if(type === "nombre"){
    const n = parseFloat(valeur);
    conteneur[cle] = isNaN(n) ? 0 : n;
  } else {
    conteneur[cle] = valeur;
  }
  // Pas de _jsonRerendre ici : on resynchronise juste le textarea caché
  // pour ne pas perdre le focus/curseur pendant la saisie au clavier.
  _jsonSyncTextarea(slug);
}
function _jsonBasculerMode(slug){
  const wrapper = document.querySelector(`.editeur-json[data-slug="${slug}"]`);
  if(!wrapper) return;
  const zoneGuidee = wrapper.querySelector(".editeur-json-guide");
  const ta = wrapper.querySelector("textarea");
  const lien = wrapper.querySelector(".lien-json-brut");
  const enModeBrut = ta.style.display !== "none";
  if(enModeBrut){
    // Retour au mode guidé : on retente de parser ce que l'utilisateur a
    // saisi/modifié en JSON brut, pour ne rien perdre.
    try{
      const valeur = ta.value.trim() === "" ? {} : JSON.parse(ta.value);
      _jsonEtats[slug] = (valeur && typeof valeur === "object") ? valeur : {};
    }catch(e){
      notifierErreur(tj("engine/static/engine/js/index_1.js:5077:23"));
      return;
    }
    ta.style.display = "none";
    zoneGuidee.style.display = "";
    lien.textContent = tj("engine/static/engine/js/index_1.js:5082:25");
    _jsonRerendre(slug);
  } else {
    zoneGuidee.style.display = "none";
    ta.style.display = "";
    lien.textContent = tj("engine/static/engine/js/index_1.js:5087:25");
  }
}
// ── Capture média directe (caméra/micro) pour les champs image/son/vidéo ────
// Ouvre le modal #modal-capture-fond, affiche l'aperçu caméra/micro en direct,
// permet de capturer une photo ou d'enregistrer un son/une vidéo, puis résout
// avec un objet File (ou null si annulé) — jamais de rejet, pour rester simple
// à appeler depuis un onclick.
let _captureStream = null;
let _captureRecorder = null;
let _captureChunks = [];
let _captureBlob = null;
let _captureMinuteur = null;
let _captureDebut = 0;
let _captureFacing = "environment"; // caméra courante : "environment" (arrière) ou "user" (avant)

function _captureContraintes(kind, facing){
  return kind === "image" ? { video: { facingMode: facing } }
    : kind === "son" ? { audio: true }
    : { video: { facingMode: facing }, audio: true };
}

function _captureArreterFlux(){
  if(_captureStream){ _captureStream.getTracks().forEach(t => t.stop()); _captureStream = null; }
  if(_captureRecorder && _captureRecorder.state !== "inactive"){ try{ _captureRecorder.stop(); }catch(e){} }
  _captureRecorder = null;
  if(_captureMinuteur){ clearInterval(_captureMinuteur); _captureMinuteur = null; }
  const ind = document.getElementById("capture-rec-indicateur");
  if(ind) ind.style.display = "none";
}

function ouvrirCaptureMedia(kind){
  return new Promise(async (resolve) => {
    const fond = document.getElementById("modal-capture-fond");
    const titre = document.getElementById("capture-titre");
    const erreur = document.getElementById("capture-erreur");
    const video = document.getElementById("capture-video");
    const photoApercu = document.getElementById("capture-photo-apercu");
    const audioApercu = document.getElementById("capture-audio-apercu");
    const videoApercu = document.getElementById("capture-video-apercu");
    const btnAction = document.getElementById("capture-btn-action");
    const btnUtiliser = document.getElementById("capture-btn-utiliser");
    const btnReprendre = document.getElementById("capture-btn-reprendre");
    const btnAnnuler = document.getElementById("capture-btn-annuler");
    const btnBasculer = document.getElementById("capture-btn-basculer");
    const canvas = document.getElementById("capture-canvas");

    _captureBlob = null; _captureChunks = []; _captureFacing = "environment";
    erreur.style.display = "none"; erreur.textContent = "";
    titre.textContent = kind === "image" ? tj("engine/static/engine/js/index_1.js:5136:45") : kind === "son" ? tj("engine/static/engine/js/index_1.js:5136:86") : tj("engine/static/engine/js/index_1.js:5136:112");
    video.style.display = "none"; photoApercu.style.display = "none";
    audioApercu.style.display = "none"; videoApercu.style.display = "none";
    audioApercu.removeAttribute("src"); videoApercu.removeAttribute("src");
    btnUtiliser.style.display = "none"; btnReprendre.style.display = "none"; btnBasculer.style.display = "none";
    btnAction.style.display = "inline-block";
    btnAction.textContent = kind === "image" ? "📸 Capturer" : tj("engine/static/engine/js/index_1.js:5142:64");
    fond.style.display = "flex";

    let enregistrementEnCours = false;

    const nettoyerEtFermer = (resultat) => {
      _captureArreterFlux();
      fond.style.display = "none";
      resolve(resultat);
    };

    let flux;
    try{
      flux = await navigator.mediaDevices.getUserMedia(_captureContraintes(kind, _captureFacing));
    }catch(err){
      erreur.style.display = "block";
      erreur.textContent = tj("engine/static/engine/js/index_1.js:5158:29") + (err && err.message ? err.message : err);
      btnAction.style.display = "none";
      btnAnnuler.onclick = () => nettoyerEtFermer(null);
      return;
    }
    _captureStream = flux;

    if(kind === "image" || kind === "video"){
      video.srcObject = flux; video.style.display = "block";
      btnBasculer.style.display = "flex";
      btnBasculer.style.alignItems = "center"; btnBasculer.style.justifyContent = "center";
    }

    btnAnnuler.onclick = () => nettoyerEtFermer(null);

    // Bascule caméra avant/arrière : redémarre le flux vidéo avec le facingMode
    // opposé, sans toucher à un éventuel enregistrement audio déjà en cours
    // (on ne propose la bascule que hors enregistrement, cf. affichage ci-dessous).
    btnBasculer.onclick = async () => {
      _captureFacing = _captureFacing === "environment" ? "user" : "environment";
      try{
        const nouveauFlux = await navigator.mediaDevices.getUserMedia(_captureContraintes(kind, _captureFacing));
        if(_captureStream) _captureStream.getTracks().forEach(t => t.stop());
        _captureStream = nouveauFlux;
        video.srcObject = nouveauFlux;
      }catch(err){
        erreur.style.display = "block";
        erreur.textContent = tj("engine/static/engine/js/index_1.js:5185:31") + (err && err.message ? err.message : err);
        _captureFacing = _captureFacing === "environment" ? "user" : "environment"; // on annule le changement
      }
    };

    btnReprendre.onclick = async () => {
      _captureBlob = null; _captureChunks = [];
      photoApercu.style.display = "none"; audioApercu.style.display = "none"; videoApercu.style.display = "none";
      btnUtiliser.style.display = "none"; btnReprendre.style.display = "none";
      btnAction.style.display = "inline-block";
      btnAction.textContent = kind === "image" ? "📸 Capturer" : tj("engine/static/engine/js/index_1.js:5195:66");
      if(!_captureStream){
        try{
          _captureStream = await navigator.mediaDevices.getUserMedia(_captureContraintes(kind, _captureFacing));
        }catch(err){ erreur.style.display="block"; erreur.textContent = tj("engine/static/engine/js/index_1.js:5199:74"); return; }
      }
      if(kind === "image" || kind === "video"){ video.srcObject = _captureStream; video.style.display = "block"; btnBasculer.style.display = "flex"; }
    };

    btnUtiliser.onclick = () => {
      if(!_captureBlob) return;
      const ext = kind === "image" ? "jpg" : (kind === "son" ? "webm" : "webm");
      const type = kind === "image" ? "image/jpeg" : (kind === "son" ? "audio/webm" : "video/webm");
      const fichier = new File([_captureBlob], `capture_${kind}_${Date.now()}.${ext}`, { type });
      nettoyerEtFermer(fichier);
    };

    if(kind === "image"){

      btnAction.onclick = () => {
        canvas.width = video.videoWidth || 640; canvas.height = video.videoHeight || 480;
        canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
        canvas.toBlob((blob) => {
          _captureBlob = blob;
          photoApercu.src = URL.createObjectURL(blob);
          video.style.display = "none"; photoApercu.style.display = "block";
          btnAction.style.display = "none"; btnUtiliser.style.display = "inline-block"; btnReprendre.style.display = "inline-block";
          btnBasculer.style.display = "none";
          if(_captureStream){ _captureStream.getTracks().forEach(t => t.stop()); _captureStream = null; }
        }, "image/jpeg", 0.92);
      };
    } else {
      // Enregistrement audio ou vidéo via MediaRecorder — un seul clic démarre,
      // le même bouton (texte changé) arrête l'enregistrement.
      btnAction.onclick = () => {
        if(!enregistrementEnCours){
          enregistrementEnCours = true;
          _captureChunks = [];
          try{
            _captureRecorder = new MediaRecorder(_captureStream);
          }catch(e){
            erreur.style.display = "block"; erreur.textContent = tj("engine/static/engine/js/index_1.js:5236:67");
            enregistrementEnCours = false; return;
          }
          _captureRecorder.ondataavailable = (e) => { if(e.data && e.data.size > 0) _captureChunks.push(e.data); };
          _captureRecorder.onstop = () => {
            _captureBlob = new Blob(_captureChunks, { type: kind === "son" ? "audio/webm" : "video/webm" });
            const url = URL.createObjectURL(_captureBlob);
            if(kind === "son"){ audioApercu.src = url; audioApercu.style.display = "block"; video.style.display = "none"; }
            else { videoApercu.src = url; videoApercu.style.display = "block"; video.style.display = "none"; }
            btnAction.style.display = "none"; btnUtiliser.style.display = "inline-block"; btnReprendre.style.display = "inline-block";
            btnBasculer.style.display = "none";
            if(_captureStream){ _captureStream.getTracks().forEach(t => t.stop()); _captureStream = null; }
          };
          _captureRecorder.start();
          btnAction.textContent = tj("engine/static/engine/js/index_1.js:5250:36");
          btnBasculer.style.display = "none"; // pas de bascule pendant l'enregistrement (couperait le flux capté)
          _captureDebut = Date.now();
          const ind = document.getElementById("capture-rec-indicateur");
          const tempsLbl = document.getElementById("capture-rec-temps");
          ind.style.display = "flex";
          _captureMinuteur = setInterval(() => {
            const s = Math.floor((Date.now() - _captureDebut) / 1000);
            tempsLbl.textContent = String(Math.floor(s/60)).padStart(2,"0") + ":" + String(s%60).padStart(2,"0");
          }, 500);
        } else {
          enregistrementEnCours = false;
          if(_captureMinuteur){ clearInterval(_captureMinuteur); _captureMinuteur = null; }
          const ind = document.getElementById("capture-rec-indicateur"); if(ind) ind.style.display = "none";
          if(_captureRecorder && _captureRecorder.state !== "inactive") _captureRecorder.stop();
        }
      };
    }
  });
}

// Déclenché par le bouton "📷/🎙️/🎬" à côté d'un input file image/son/vidéo :
// ouvre la capture, puis injecte le résultat directement dans l'input file
// existant (via DataTransfer) pour que tout le code d'envoi (FormData sur
// el.files[0]) fonctionne sans aucune autre modification.
async function _declencherCaptureMedia(btn){
  const slug = btn.dataset.slug, kind = btn.dataset.kind;
  const input = document.getElementById("champ_"+slug);
  const fichier = await ouvrirCaptureMedia(kind);
  if(fichier && input){
    const dt = new DataTransfer();
    dt.items.add(fichier);
    input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }
}

// Sélecteur de lieu (_choisirSurCarte, _positionGPS) : static/engine/js/selecteur_lieu.js, partagé avec la page client.

// Préremplit un input (généré par champVersInput) avec la valeur actuelle
// d'une ligne, pour le modal d'édition.
function remplirChampInput(c, valeur, conteneur){
  conteneur = conteneur || document;
  const el = conteneur.querySelector("#champ_"+c.slug);
  if(!el) return;
  if(valeur === null || valeur === undefined) return;
  if(c.type === "booleen" || c.type === "switch"){
    el.checked = valeur === true || valeur === "1" || valeur === "true" || valeur === "vrai";
  } else if(c.type === "choix_multiple"){
    const valeurs = Array.isArray(valeur) ? valeur : String(valeur).split(",").map(v=>v.trim());
    for(const opt of el.options) opt.selected = valeurs.includes(opt.value);
  } else if(c.type === "barre_progression"){
    el.value = valeur;
  } else if(c.type === "colonne"){
    el.dataset.valeur = valeur; _peuplerMenuColonne(el);
  } else if(c.type === "relation"){
    const hidden = conteneur.querySelector("#champ_"+c.slug);
    const labelEl = conteneur.querySelector("#champ_"+c.slug+"_label");
    const wrapper = conteneur.querySelector("#champ_"+c.slug+"_wrapper");
    const direct = conteneur.querySelector("#champ_"+c.slug+"_direct");
    if(hidden) hidden.value = valeur || "";
    if(labelEl && valeur !== null && valeur !== undefined && valeur !== ""){
      // Le label résolu vient de ligne[slug__label] s'il est passé dans wrapper
      const labelResolu = wrapper?.dataset.labelResolu || String(valeur);
      labelEl.textContent = labelResolu;
      labelEl.style.color = "var(--text)";
      labelEl.style.fontStyle = "normal";
    }
    if(direct){
      // Valeur de référence pour détecter si l'utilisateur a modifié
      // directement la case (voir btn-enregistrer-ligne)
      direct.value = valeur === null || valeur === undefined ? "" : String(valeur);
      direct.dataset.baseline = direct.value;
    }
  } else if(TYPES_TENSEUR.includes(c.type)){
    GYTenseur.remplir(el, valeur);
  } else if(TYPES_FICHIER_LOCAL.includes(c.type)){
    // Pas de préremplissage possible pour un <input type=file> : on laisse vide,
    // le fichier existant est conservé si l'utilisateur n'en choisit pas un nouveau.
  } else if(c.type === "json"){
    // `valeur` arrive déjà parsée (objet/liste) depuis l'API (valeur_typee()
    // fait le json.loads côté serveur) ; on gère quand même le cas d'une
    // chaîne brute par prudence (ex: valeur invalide renvoyée telle quelle).
    let donnee = valeur;
    if(typeof donnee === "string"){
      try{ donnee = donnee.trim() === "" ? {} : JSON.parse(donnee); }catch(e){ donnee = {}; }
    }
    if(donnee === null || typeof donnee !== "object") donnee = {};
    _jsonEtats[c.slug] = donnee;
    _jsonRerendre(c.slug);
    // S'assure que l'éditeur guidé est bien affiché (et pas resté en mode
    // JSON brut suite à une précédente ouverture de ce même formulaire).
    const wrapper = conteneur.querySelector(`.editeur-json[data-slug="${c.slug}"]`);
    if(wrapper){
      const guide = wrapper.querySelector(".editeur-json-guide");
      const zoneBrute = wrapper.querySelector("textarea");
      const lien = wrapper.querySelector(".lien-json-brut");
      if(guide) guide.style.display = "";
      if(zoneBrute) zoneBrute.style.display = "none";
      if(lien) lien.textContent = tj("engine/static/engine/js/index_1.js:5353:36");
    }
  } else {
    el.value = valeur;
  }
}
const TYPES_FICHIER_LOCAL = ["fichier","image","son","video"];
// Pour un champ relation à la CRÉATION d'une ligne : il faut toujours l'_id
// de la ligne choisie via le picker (pour établir le lien), et en plus, si
// la colonne est partagée (bidirectionnelle) et que l'utilisateur a modifié
// le champ d'édition directe, la valeur de remplacement à appliquer une fois
// le lien créé. Retourne un objet {slug: id, "slug__valeur_directe"?: val}
// à fusionner dans le payload (JSON ou FormData) — voir call sites.
function _relationValeursPourCreation(c, conteneur){
  conteneur = conteneur || document;
  const hidden = conteneur.querySelector("#champ_"+c.slug);
  const idChoisi = hidden ? hidden.value : "";
  const sortie = {};
  sortie[c.slug] = idChoisi;
  if(c.relation_bidirectionnelle && idChoisi){
    const directEl = conteneur.querySelector("#champ_"+c.slug+"_direct");
    if(directEl && directEl.value !== directEl.dataset.baseline && directEl.value.trim() !== ""){
      sortie[c.slug+"__valeur_directe"] = directEl.value;
    }
  }
  return sortie;
}
// Branche les event listeners des widgets personnalisés (switch, barre de
// progression) APRÈS insertion du HTML. À appeler à chaque fois qu'on
// insère un nouveau formulaire dans #zone-contenu.
// ── Picker relation ────────────────────────────────────────────────────────
// Données chargées une fois par table cible, cachées dans _pickerCache
const _pickerCache = {};

let _pickerCallback = null; // fonction appelée quand l'utilisateur valide
async function _ouvrirPickerRelation(bouton){
  const wrapper = bouton.closest(".champ-relation-picker");
  if(!wrapper) return;
  const tableId  = wrapper.dataset.tableId;
  const colSrc   = wrapper.dataset.colonneSource || "";
  const colAff   = wrapper.dataset.champAffichage || "";
  const slug     = wrapper.dataset.slug;
  if(!tableId){ notifierErreur(tj("engine/static/engine/js/index_1.js:5395:33")); return; }

  // Charger les données si pas encore en cache
  if(!_pickerCache[tableId]){
    bouton.textContent = "⏳";
    try {
      _pickerCache[tableId] = await api(`/api/tables/${tableId}/lignes-relation/`);
    } catch(e) { bouton.textContent = tj("engine/static/engine/js/index_1.js:5402:40"); return; }
    bouton.textContent = tj("engine/static/engine/js/index_1.js:5403:27");
  }

  const data = _pickerCache[tableId];
  const champs = data.champs; // [{slug, nom, type}]

  // Colonne source (valeur copiée) et colonne label
  const champSrc = colSrc ? champs.find(c=>c.slug===colSrc) : champs[0];
  const champAff = colAff ? champs.find(c=>c.slug===colAff) : champs[0];

  // Remplir le modal
  document.getElementById("picker-table-nom").textContent = data.table_nom;
  const infoCol = document.getElementById("picker-info-colonne");
  infoCol.textContent = champSrc
    ? tj("engine/static/engine/js/index_1.js:5417:8", {v1: champSrc.nom, v2: (champAff||champSrc).nom})
    : tj("engine/static/engine/js/index_1.js:5418:8");

  // En-tête tableau
  const thead = document.getElementById("picker-thead");
  thead.innerHTML = `<tr>${champs.map(c=>
    `<th style="padding:6px 10px;text-align:left;font-size:11px;font-weight:600;color:var(--muted);border-bottom:1px solid var(--border);white-space:nowrap;
      ${c.slug===champSrc?.slug?'color:var(--accent)':''}
      ${c.slug===champAff?.slug?'text-decoration:underline dotted':''}
    ">${escapeHtml(c.nom)}</th>`
  ).join("")}</tr>`;

  // Callback de sélection
  _pickerCallback = (ligne) => {
    const valeurCopiee = ligne[champSrc?.slug] ?? "";
    const labelAffiche = ligne[champAff?.slug ?? champSrc?.slug] ?? valeurCopiee;
    const hidden = document.getElementById("champ_"+slug);
    const labelEl = document.getElementById("champ_"+slug+"_label");
    // Stocker l'_id de la ligne (pour la FK) ET la valeur copiée
    // On stocke l'_id dans le hidden pour que _creer_lien_champ sache quelle ligne
    if(hidden){ hidden.value = ligne._id; }
    // Stocker la valeur copiée dans un data attr pour la récupérer à la soumission
    wrapper.dataset.valeurCopiee  = String(valeurCopiee ?? "");
    wrapper.dataset.labelResolu   = String(labelAffiche ?? "");
    if(labelEl){
      labelEl.textContent = `${escapeHtml(String(labelAffiche))}`;
      labelEl.style.color = "var(--text)";
      labelEl.style.fontStyle = "normal";
    }
    _fermerPicker();
  };

  document.getElementById("modal-picker-relation").dataset.tableId = tableId;
  _afficherLignesPicker(data.lignes, champs, "");
  document.getElementById("picker-recherche").value = "";
  document.getElementById("modal-picker-relation").style.display = "flex";
  document.getElementById("picker-recherche").focus();
}
function _afficherLignesPicker(lignes, champs, recherche){
  const tbody = document.getElementById("picker-tbody");
  const terme = recherche.trim().toLowerCase();
  const lignesFiltrees = terme
    ? lignes.filter(l => champs.some(c => String(l[c.slug]||"").toLowerCase().includes(terme)))
    : lignes;

  tbody.innerHTML = "";
  for(const ligne of lignesFiltrees){
    const tr = document.createElement("tr");
    tr.style.cssText = "cursor:pointer;border-bottom:1px solid var(--bordure-forte,#0a0a0a)";
    tr.onmouseenter = () => { tr.style.background="var(--fond-tres-sombre,#0a0a0a)"; };
    tr.onmouseleave = () => { tr.style.background=""; };
    tr.onclick = () => _pickerCallback && _pickerCallback(ligne);
    for(const c of champs){
      const td = document.createElement("td");
      td.style.cssText = "padding:7px 10px;font-size:12px;white-space:nowrap;max-width:200px;overflow:hidden;text-overflow:ellipsis";
      const val = ligne[c.slug];
      // Surligner le terme recherché
      const txt = val !== null && val !== undefined ? String(val) : "";
      if(terme && txt.toLowerCase().includes(terme)){
        const idx = txt.toLowerCase().indexOf(terme);
        td.innerHTML = escapeHtml(txt.slice(0,idx))
          + `<mark style="background:color-mix(in srgb,var(--accent,#6bcfff) 20%,transparent);color:var(--accent)">${escapeHtml(txt.slice(idx,idx+terme.length))}</mark>`
          + escapeHtml(txt.slice(idx+terme.length));
      } else {
        td.textContent = txt;
      }
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  document.getElementById("picker-count").textContent =
    (() => { const n = lignesFiltrees.length, pl = n !== 1; return tj(terme ? (pl ? "engine/static/engine/js/index_1.js:lignes_trouvees" : "engine/static/engine/js/index_1.js:ligne_trouvee") : (pl ? "engine/static/engine/js/index_1.js:lignes_disponibles" : "engine/static/engine/js/index_1.js:ligne_disponible"), {v1: n}); })();
}
function _fermerPicker(){
  document.getElementById("modal-picker-relation").style.display = "none";
  _pickerCallback = null;
}
document.getElementById("btn-fermer-picker").onclick = _fermerPicker;
document.getElementById("modal-picker-relation").onclick = (e) => {
  if(e.target === e.currentTarget) _fermerPicker();
};

// ── Partage de LIGNE / CELLULE ───────────────────────────────────────────
// Modal générique (mode "ligne" ou "cellule") qui permet de relier la ligne
// (ou la cellule) courante à une ligne (ou cellule) d'une autre table — voir
// api_partage_ligne / api_partage_cellule côté serveur. Réutilise l'endpoint
// déjà existant /api/tables/<id>/lignes-relation/ pour afficher les lignes
// de la table cible choisie (même principe que le picker de relation).
let _partageEtat = null; // {mode, table, ligneId, champId, groupeId}

function _urlPartage(etat){
  return etat.mode === "ligne"
    ? `/api/tables/${etat.table.id}/lignes/${etat.ligneId}/partage-ligne/`
    : `/api/tables/${etat.table.id}/lignes/${etat.ligneId}/champs/${etat.champId}/partage-cellule/`;
}

function _ouvrirModalPartage(mode, table, ligneId, champId){
  _partageEtat = { mode, table, ligneId, champId, groupeId: null, cibles: [] };
  _rafraichirCiblesPartage();
  const champNom = mode === "cellule" ? (table.champs.find(c => c.id === champId)||{}).nom || "" : "";
  document.getElementById("partage-titre").textContent = mode === "ligne"
    ? tj("engine/static/engine/js/index_1.js:5518:8", {v1: table.nom})
    : tj("engine/static/engine/js/index_1.js:5519:8", {v1: champNom, v2: table.nom});
  document.getElementById("modal-partage-fond").style.display = "flex";
  _rafraichirMembresPartage();
  const selTable = document.getElementById("partage-select-table");
  selTable.innerHTML = `<option value="">${tj("engine/static/engine/js/index_1.js:5523:43")}</option>` +
    tables.map(t => `<option value="${t.id}">${escapeHtml(t.nom)}</option>`).join("");
  selTable.value = "";
}

async function _rafraichirMembresPartage(){
  const etat = _partageEtat;
  const zone = document.getElementById("partage-membres-actuels");
  const btnRetirer = document.getElementById("btn-partage-retirer");
  zone.textContent = tj("engine/static/engine/js/index_1.js:5532:23");
  let data;
  try { data = await api(_urlPartage(etat)); }
  catch(e){ zone.textContent = tj("engine/static/engine/js/index_1.js:5535:33"); return; }
  etat.groupeId = data.groupe_id;
  if(!data.membres || !data.membres.length){
    zone.innerHTML = `<div style="color:var(--muted)">${tj("engine/static/engine/js/index_1.js:5538:56")}</div>`;
    btnRetirer.style.display = "none";
  } else {
    zone.innerHTML = data.membres.map(m => `
      <div style="padding:3px 0">🔗 ${escapeHtml(m.table_nom)} — ${escapeHtml(m.ligne_label || ("#"+m.ligne_id))}${m.champ_nom ? " · " + escapeHtml(m.champ_nom) : ""}</div>
    `).join("");
    btnRetirer.style.display = "";
  }
}

// Choisir la table cible ouvre le SÉLECTEUR GRAPHIQUE des tables de calcul
// (_ouvrirSelecteurCible, réutilisé tel quel — voir ce nom plus haut) :
// clic sur une cellule = ligne + colonne, exactement comme pour cibler une
// formule, mais appliqué directement sur la table choisie ci-dessous.
document.getElementById("partage-select-table").onchange = function(){
  const cibleTableId = parseInt(this.value, 10);
  if(!cibleTableId) return;
  const etat = _partageEtat;
  const cibleTable = tables.find(t => t.id === cibleTableId);
  if(!cibleTable){ notifierErreur(tj("engine/static/engine/js/index_1.js:5557:36")); return; }
  _ouvrirSelecteurCible(null, cibleTable, cibleTable.champs, {cible_type: "colonne", types_cibles_compatibles: []},
    (selections, champs, lignes) => {
      const s = selections[0];
      if(selections.length !== 1 || s.ligneDebut === null || s.ligneDebut !== s.ligneFin){
        notifierErreur(tj("engine/static/engine/js/index_1.js:5562:25"));
        return false;
      }
      const ligneCible = lignes[s.ligneDebut];
      const champCible = champs[s.colIndex];
      const idChamp = etat.mode === "cellule" ? champCible.id : null;
      if(idChamp === null && etat.cibles.some(c => c.ligne_id === ligneCible._id)
         || idChamp !== null && etat.cibles.some(c => c.ligne_id === ligneCible._id && c.champ_id === idChamp)){
        notifierErreur(tj("engine/static/engine/js/index_1.js:5570:25")); return false;
      }
      etat.cibles.push({
        table_id: cibleTableId, ligne_id: ligneCible._id, champ_id: idChamp,
        label: cibleTable.nom + (idChamp ? " · " + champCible.nom : "") + tj("engine/static/engine/js/index_1.js:5574:76") + ligneCible._id,
      });
      _rafraichirCiblesPartage();
      document.getElementById("partage-select-table").value = "";
    }
  );
};

// Partage EN GROUPE (comme les colonnes) : on accumule plusieurs cibles, puis un seul envoi.
function _rafraichirCiblesPartage(){
  const etat = _partageEtat;
  const zone = document.getElementById("partage-cibles-attente");
  const btn = document.getElementById("btn-partage-valider");
  if(!etat || !etat.cibles.length){ zone.innerHTML = ""; btn.style.display = "none"; return; }
  zone.innerHTML = etat.cibles.map((c, i) => `
    <div style="padding:2px 0">➕ ${escapeHtml(c.label)}
      <a href="#" data-retirer-cible="${i}" class="btn petit" style="margin-left:6px">✕</a></div>`).join("");
  zone.querySelectorAll("[data-retirer-cible]").forEach(a => a.onclick = (e) => {
    e.preventDefault(); etat.cibles.splice(parseInt(a.dataset.retirerCible, 10), 1); _rafraichirCiblesPartage();
  });
  btn.style.display = "";
}

document.getElementById("btn-partage-valider").onclick = () => _validerPartage();

async function _validerPartage(){
  const etat = _partageEtat;
  if(!etat || !etat.cibles.length) return;
  const cibles = etat.cibles.map(c => etat.mode === "ligne"
    ? { table_id: c.table_id, ligne_id: c.ligne_id }
    : { table_id: c.table_id, ligne_id: c.ligne_id, champ_id: c.champ_id });
  try {
    await api(_urlPartage(etat), { method: "POST", body: JSON.stringify({ cibles }) });
    notifierSucces(tj("engine/static/engine/js/index_1.js:5607:21"));
    etat.cibles = []; _rafraichirCiblesPartage();
    await _rafraichirMembresPartage();
    if(modeVue === "dashboard" && tableActive === etat.table.slug) afficherTable(etat.table.slug);
  } catch(e) { /* déjà notifié */ }
}

document.getElementById("btn-partage-retirer").onclick = async () => {
  const etat = _partageEtat;
  if(!etat) return;
  try {
    await api(_urlPartage(etat), { method: "DELETE" });
    notifierSucces(tj("engine/static/engine/js/index_1.js:5619:21"));
    await _rafraichirMembresPartage();
    if(modeVue === "dashboard" && tableActive === etat.table.slug) afficherTable(etat.table.slug);
  } catch(e) { /* déjà notifié */ }
};

function _fermerModalPartage(){
  document.getElementById("modal-partage-fond").style.display = "none";
  _partageEtat = null;
}
document.getElementById("btn-fermer-partage").onclick = _fermerModalPartage;
document.getElementById("modal-partage-fond").onclick = (e) => {
  if(e.target === e.currentTarget) _fermerModalPartage();
};

// ── Visionneuse : espace dédié pour ouvrir en grand une image/son/vidéo
// depuis la vue Données (bouton "⤢ Ouvrir" injecté par valeurCellule).
// Délégation : navigation vers table source (relation / colonne partagée)
// Branché sur document car les boutons sont injectés dynamiquement.
document.addEventListener("click", function(e){
  const btn = e.target.closest(".btn-nav-table-source");
  if(!btn) return;
  e.stopPropagation();
  // Colonne partagée : si plusieurs tables membres, afficher un mini-menu
  const membresRaw = btn.dataset.partageMembers || btn.dataset.partageMembers || "";
  let membres = [];
  try { membres = JSON.parse(btn.dataset.partageMembers||"[]"); } catch(_){}
  if(membres.length > 1){
    // Plusieurs tables — afficher un popover de choix
    let existant = document.getElementById("popover-nav-table");
    if(existant) existant.remove();
    const pop = document.createElement("div");
    pop.id = "popover-nav-table";
    pop.style.cssText = "position:fixed;z-index:9999;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:8px;box-shadow:0 4px 20px color-mix(in srgb,var(--ombre,#000000) 50%,transparent);min-width:160px";
    const rect = btn.getBoundingClientRect();
    pop.style.top = (rect.bottom + 6) + "px";
    pop.style.left = rect.left + "px";
    pop.innerHTML = `<div style="font-size:11px;color:var(--muted);margin-bottom:6px;padding-bottom:4px;border-bottom:1px solid var(--border)">${tj("engine/static/engine/js/index_1.js:5656:145")}</div>` +
      membres.map(m => `<div style="padding:4px 6px;cursor:pointer;border-radius:4px;font-size:12px" onmouseenter="this.style.background='var(--hover)'" onmouseleave="this.style.background=''" onclick="document.getElementById('popover-nav-table')?.remove(); if('${escapeHtml(m.slug||"")}'){modeVue='dashboard';tableActive='${escapeHtml(m.slug||"")}';afficherTableauBord('${escapeHtml(m.slug||"")}');rafraichirTables();}">${escapeHtml(m.nom)}</div>`).join("");
    document.body.appendChild(pop);
    const fermer = ev => { if(!pop.contains(ev.target)){ pop.remove(); document.removeEventListener("click", fermer); } };
    setTimeout(() => document.addEventListener("click", fermer), 10);
    return;
  }
  const slug = btn.dataset.tableSlug || (membres[0]||{}).slug || "";
  if(!slug) return;
  const t = tables.find(t => t.slug === slug);
  if(!t){ notifierErreur(tj("engine/static/engine/js/index_1.js:5666:27")); return; }
  modeVue = "dashboard"; tableActive = slug;
  afficherTableauBord(slug);
  rafraichirTables();
  if(window.innerWidth <= 768) fermerSidebar();
});

// Délégation d'événement (les cellules sont réinjectées en innerHTML à
// chaque rendu de tableau, un listener posé une fois au chargement suffit).
function ouvrirVisionneuse(type, url, titre){
  document.getElementById("visionneuse-titre").textContent = titre || tj("engine/static/engine/js/index_1.js:5676:72");
  document.getElementById("visionneuse-lien-original").href = url;
  const img = document.getElementById("visionneuse-image");
  const video = document.getElementById("visionneuse-video");
  const audio = document.getElementById("visionneuse-audio");
  const lisible = document.getElementById("visionneuse-lisible");
  img.style.display = "none"; video.style.display = "none"; audio.style.display = "none";
  video.pause(); audio.pause();
  video.removeAttribute("src"); audio.removeAttribute("src");
  ApercuFichier.vider(lisible); lisible.style.display = "none";
  // La fenêtre est affichée AVANT de monter le cadre : un PDF chargé dans un cadre masqué (display:none) n'est pas rendu par les
  // navigateurs mobiles, qui retombent sur une carte « Ouvrir » (téléchargement) au lieu d'afficher le document.
  document.getElementById("modal-visionneuse-fond").style.display = "flex";
  if(type === "pdf" || type === "texte"){
    lisible.style.display = "block"; ApercuFichier.monter(lisible, url, type);
  } else if(type === "image"){
    img.src = url; img.style.display = "block";
  } else if(type === "video"){
    video.src = url; video.style.display = "block";
  } else if(type === "son"){
    audio.src = url; audio.style.display = "block";
  }
}
function _fermerVisionneuse(){
  document.getElementById("modal-visionneuse-fond").style.display = "none";
  const video = document.getElementById("visionneuse-video");
  const audio = document.getElementById("visionneuse-audio");
  video.pause(); audio.pause();
  video.removeAttribute("src"); audio.removeAttribute("src");
  const lisible = document.getElementById("visionneuse-lisible");
  ApercuFichier.vider(lisible); lisible.style.display = "none";
}
document.getElementById("btn-fermer-visionneuse").onclick = _fermerVisionneuse;
document.getElementById("modal-visionneuse-fond").onclick = (e) => {
  if(e.target === e.currentTarget) _fermerVisionneuse();
};
document.addEventListener("keydown", (e) => {
  if(e.key === "Escape" && document.getElementById("modal-visionneuse-fond").style.display === "flex"){
    _fermerVisionneuse();
  }
});
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".btn-ouvrir-media");
  if(!btn) return;
  ouvrirVisionneuse(btn.dataset.type, btn.dataset.url, btn.dataset.titre);
});
document.getElementById("picker-recherche").oninput = (e) => {
  const tableId = document.getElementById("modal-picker-relation").dataset.tableId;
  const entry = tableId && _pickerCache[tableId];
  if(entry) _afficherLignesPicker(entry.lignes, entry.champs, e.target.value);
};
function initialiserWidgets(){
  // Switch : la checkbox est visuellement cachée, c'est le <label>
  // entier qui est cliquable. Le label contient déjà le `for` implicite
  // via l'imbrication, mais on ajoute un toggle manuel pour la décocher
  // quand on clique (le click bubbling suffit en fait grâce à l'imbrication
  // input dans label — on garde ce comportement par défaut).
  for(const cb of document.querySelectorAll(".widget-switch input[type=checkbox]")){
    if(cb.dataset.swInit) continue;
    cb.dataset.swInit = "1";
    const piste = cb.parentElement.querySelector(".switch-piste");
    const boule = cb.parentElement.querySelector(".switch-boule");
    const etiq = cb.parentElement.querySelector(".switch-etiquette");
    const maj = () => {
      if(cb.checked){
        piste.style.background = gyCouleur("--fond-succes","#0a2a1a");
        boule.style.left = "18px";
        boule.style.background = gyCouleur("--ok","#2ecc71");
        etiq.textContent = tj("engine/static/engine/js/index_1.js:5736:29");
        etiq.style.color = "var(--ok)";
      } else {
        piste.style.background = gyCouleur("--fond-info","#3a5060");
        boule.style.left = "2px";
        boule.style.background = gyCouleur("--fond-releve","#555555");
        etiq.textContent = tj("engine/static/engine/js/index_1.js:5742:29");
        etiq.style.color = "var(--muted)";
      }
    };
    cb.addEventListener("change", maj);
    maj();
  }
  // Barre de progression : l'input range a un voisin `.barre-pct` qui doit
  // afficher la valeur live. On le retrouve via le parent `.widget-barre`.
  for(const wrap of document.querySelectorAll(".widget-barre")){
    const inp = wrap.querySelector("input[type=range]");
    const pct = wrap.querySelector(".barre-pct");
    if(!inp || !pct || inp.dataset.bpInit) continue;
    inp.dataset.bpInit = "1";
    pct.textContent = inp.value + "%";
    inp.addEventListener("input", () => { pct.textContent = inp.value + "%"; });
  }
}
function _champsOptionsHtml(champs, selSlug, placeholder){
  return `<option value="">${placeholder}</option>`
    + (champs||[]).map(c=>`<option value="${c.slug}" ${c.slug===selSlug?"selected":""}>${escapeHtml(c.nom)} (${c.type})</option>`).join("");
}
// Champ « colonne » : même volet que « relation » ; seuls les intitulés changent (colonne du menu + colonne nominale exigée).
function _majConfigRelation(relConfig, type, tableSel, affSel, srcSel){
  const estCol = type === "colonne";
  relConfig.style.display = (type === "relation" || estCol) ? "flex" : "none";
  const bidir = relConfig.querySelector(".champ-relation-bidir-lbl");
  if(bidir) bidir.style.display = estCol ? "none" : "";
  const cb = relConfig.querySelector(".champ-relation-bidirectionnelle");
  if(estCol && cb) cb.checked = false;
  if(tableSel.value) _chargerChampsRelation(tableSel.value, affSel, srcSel, affSel.value, srcSel.value);
}
async function _chargerChampsRelation(tableId, selAff, selSrc, slugAff, slugSrc){
  const _l = selAff.closest(".ligne-structure, .ligne-champ-modal");
  const _col = !!_l && (_l.querySelector(".structure-type, .champ-type") || {}).value === "colonne";
  const phAff = _col ? tj("engine/static/engine/js/index_1.js:5777:25") : tj("engine/static/engine/js/index_1.js:5777:59");
  const phSrc = _col ? tj("engine/static/engine/js/index_1.js:5778:25") : tj("engine/static/engine/js/index_1.js:5778:49");
  selAff.innerHTML = `<option value="">${phAff}</option>`;
  selSrc.innerHTML = `<option value="">${phSrc}</option>`;
  selAff.disabled = selSrc.disabled = !tableId;
  if(!tableId) return;
  // Utiliser le cache picker si dispo, sinon faire un appel léger
  let champs = [];
  if(_pickerCache[tableId]){
    champs = _pickerCache[tableId].champs;
  } else {
    try {
      const res = await api(`/api/tables/${tableId}/lignes-relation/`);
      _pickerCache[tableId] = res;
      champs = res.champs;
    } catch(e){ return; }
  }
  selAff.innerHTML = _champsOptionsHtml(champs, slugAff, phAff);
  selSrc.innerHTML = _champsOptionsHtml(champs, slugSrc, phSrc);
  selAff.disabled = selSrc.disabled = false;
}
function ligneCiblePartage(){
  const ligne = document.createElement("div");
  ligne.className = "champ-partage-cible-ligne";
  ligne.style.cssText = "display:flex;gap:6px;align-items:center;flex-wrap:wrap";
  ligne.innerHTML = `
    <select class="champ-partage-table-cible" style="flex:2;min-width:140px">
      <option value="">${tj("engine/static/engine/js/index_1.js:5804:25")}</option>
      ${tables.map(t=>`<option value="${t.id}">${escapeHtml(t.nom)}</option>`).join("")}
    </select>
    <select class="champ-partage-colonne-cible" style="flex:2;min-width:130px" disabled>
      <option value="">${tj("engine/static/engine/js/index_1.js:5808:25")}</option>
    </select>
    <button type="button" class="danger champ-partage-retirer-cible" title="${tj("engine/static/engine/js/index_1.js:5810:78")}" style="padding:4px 8px">✕</button>`;
  const tableSel = ligne.querySelector(".champ-partage-table-cible");
  const colSel   = ligne.querySelector(".champ-partage-colonne-cible");
  tableSel.onchange = () => _chargerColonnesPartage(tableSel.value, colSel);
  ligne.querySelector(".champ-partage-retirer-cible").onclick = () => ligne.remove();
  return ligne;
}
async function _chargerColonnesPartage(tableId, selCol, slugCol){
  selCol.innerHTML = `<option value="">${tj("engine/static/engine/js/index_1.js:5818:41")}</option>`;
  selCol.disabled = !tableId;
  if(!tableId) return;
  let champs = [];
  if(_pickerCache[tableId]){
    champs = _pickerCache[tableId].champs;
  } else {
    try {
      const res = await api(`/api/tables/${tableId}/lignes-relation/`);
      _pickerCache[tableId] = res;
      champs = res.champs;
    } catch(e){ return; }
  }
  selCol.innerHTML = _champsOptionsHtml(champs, slugCol||"", tj("engine/static/engine/js/index_1.js:5831:63"));
  selCol.disabled = false;
}
function ligneChampVide(){
  const div = document.createElement("div"); div.className = "ligne-champ-modal";
  div.innerHTML = `
    <input placeholder="${tj("engine/static/engine/js/index_1.js:5837:26")}" class="champ-nom">
    <select class="champ-type">${TYPES_CHAMPS.map(t=>`<option value="${t}">${t}</option>`).join("")}</select>
    <input placeholder="${tj("engine/static/engine/js/index_1.js:6168:25")}" class="champ-options" style="display:none">
    <input type="number" min="1" step="1" class="champ-taille-max" placeholder="${tj("engine/static/engine/js/index_1.js:taille_max_mo")}" title="${tj("engine/static/engine/js/index_1.js:taille_max_mo")}" value="" style="display:none;width:150px">
    <label class="chk champ-obligatoire-lbl" title="${tj("engine/static/engine/js/index_1.js:5840:54")}"><input type="checkbox" class="champ-obligatoire" checked> *</label>
    <button type="button" class="danger champ-supprimer" title="${tj("engine/static/engine/js/index_1.js:5841:66")}">🗑</button>
    <div class="champ-relation-config" style="display:none">
        <select class="champ-relation-table-cible" style="flex:2;min-width:140px">
          <option value="">${tj("engine/static/engine/js/index_1.js:5844:29")}</option>
          ${tables.map(t=>`<option value="${t.id}">${escapeHtml(t.nom)}</option>`).join("")}
        </select>
        <select class="champ-relation-champ-aff" style="flex:2;min-width:130px" disabled>
          <option value="">${tj("engine/static/engine/js/index_1.js:5848:29")}</option>
        </select>
        <select class="champ-relation-col-src" style="flex:2;min-width:130px" disabled>
          <option value="">${tj("engine/static/engine/js/index_1.js:5851:29")}</option>
        </select>
        <label class="chk champ-relation-bidir-lbl" title="${tj("engine/static/engine/js/index_1.js:5853:61")}">
          <input type="checkbox" class="champ-relation-bidirectionnelle"> ${tj("engine/static/engine/js/index_1.js:5854:76")}
        </label>
      </div>`;
  div.querySelector(".champ-supprimer").onclick = () => div.remove();
  const typeSelect  = div.querySelector(".champ-type");
  const optionsInp  = div.querySelector(".champ-options");
  const relConfig   = div.querySelector(".champ-relation-config");
  const tableSel    = div.querySelector(".champ-relation-table-cible");
  const affSel      = div.querySelector(".champ-relation-champ-aff");
  const srcSel      = div.querySelector(".champ-relation-col-src");

  typeSelect.onchange = () => {
    div.querySelector(".champ-taille-max").style.display = TYPES_FICHIER_LOCAL.includes(typeSelect.value) ? "" : "none";
    optionsInp.style.display = TYPES_AVEC_OPTIONS.includes(typeSelect.value) ? "" : "none";
    optionsInp.placeholder = TYPES_TENSEUR.includes(typeSelect.value)
      ? tj("engine/static/engine/js/index_1.js:5868:10") : typeSelect.value === "constante" ? tj("engine/static/engine/js/index_1.js:5868:75") : "options: a, b, c";
    _majConfigRelation(relConfig, typeSelect.value, tableSel, affSel, srcSel);
  };
  tableSel.onchange = () => _chargerChampsRelation(tableSel.value, affSel, srcSel, "", "");
  return div;
}
function ouvrirModalLigne(table, ligne, ligneId, slug){
  const zone = document.getElementById("modal-ligne-champs");
  zone.innerHTML = table.champs.map(c =>
    `<div class="champ-item" data-champ-slug="${escapeHtml(c.slug)}"><label${_styleLabelChamp(c)}>${escapeHtml(c.nom)}${c.obligatoire ? ' <span class="obligatoire">*</span>' : ''}${_btnCouleurChamp(table, c)}
      ${ligneId ? `<button type="button" class="btn-partager-cellule" data-champ-id="${c.id}" title="${tj("engine/static/engine/js/index_1.js:a:5890:101")}" style="background:transparent;border:none;color:var(--muted);cursor:pointer;font-size:11px;padding:0 0 0 4px">🔗</button>` : ""}
      </label>${champVersInput(c)}</div>`
  ).join("");
  initialiserWidgets();
  if(ligneId){
    zone.querySelectorAll(".btn-partager-cellule").forEach(btn => {
      btn.onclick = () => _ouvrirModalPartage("cellule", table, ligneId, parseInt(btn.dataset.champId, 10));
    });
  }
  // Pré-remplir les valeurs existantes (édition)
  for(const c of table.champs){
    const val = ligne[c.slug];
    remplirChampInput(c, val, zone);
    // Pour les champs relation, pré-remplir le label résolu
    if(c.type === "relation" && ligne[c.slug + "__label"]){
      const wrapper = zone.querySelector("#champ_"+c.slug+"_wrapper");
      if(wrapper) wrapper.dataset.labelResolu = ligne[c.slug + "__label"];
      const labelEl = zone.querySelector("#champ_"+c.slug+"_label");
      if(labelEl && val !== null && val !== undefined && val !== ""){
        labelEl.textContent = ligne[c.slug + "__label"];
        labelEl.style.color = "var(--text)";
        labelEl.style.fontStyle = "normal";
      }
    }
  }
  // Stocker les champs frais dans le modal pour btn-enregistrer-ligne
  const fond = document.getElementById("modal-ligne-fond");
  fond.style.display = "flex";
  fond.dataset.tableId = table.id;
  fond.dataset.ligneId = ligneId;
  fond.dataset.slug = slug;
  fond._champsTable = table.champs; // référence directe aux champs frais
}
document.getElementById("btn-annuler-ligne").onclick = () => {
  document.getElementById("modal-ligne-fond").style.display = "none";
};
document.getElementById("btn-enregistrer-ligne").onclick = async (ev) => {
  const fond = document.getElementById("modal-ligne-fond");
  const zone = document.getElementById("modal-ligne-champs");
  const tableId = fond.dataset.tableId, ligneId = fond.dataset.ligneId, slug = fond.dataset.slug;
  const table = tables.find(t => String(t.id) === String(tableId));
  const champs = fond._champsTable || (table ? table.champs : []);

  let toutesLesCasesValides = true;
  for(const c of champs){
    const el = zone.querySelector("#champ_"+c.slug);
    if(!el) continue;
      if(c.type === "formule" || c.type === "constante" || c.type === "horodatage" || c.type === "id_client") continue;
    el.classList.remove("champ-invalide");
    if(!c.obligatoire || TYPES_FICHIER_LOCAL.includes(c.type)) continue;
    let vide;
    if(TYPES_BOOLEEN.includes(c.type)) vide = false;
    else if(c.type === "choix_multiple") vide = el.selectedOptions.length === 0;
    else vide = (el.value || "").trim() === "";
    if(vide){ el.classList.add("champ-invalide"); toutesLesCasesValides = false; }
  }
  if(!toutesLesCasesValides){ notifierErreur(tj("engine/static/engine/js/index_1.js:5934:47")); return; }

  const aDesFichiers = champs.some(c => TYPES_FICHIER_LOCAL.includes(c.type) && zone.querySelector("#champ_"+c.slug)?.files?.length);
  ev.target.textContent = "…";
  try {
    if(aDesFichiers){
      const fd = new FormData();
      for(const c of champs){
        const el = zone.querySelector("#champ_"+c.slug);
        if(!el) continue;
      if(c.type === "formule" || c.type === "constante" || c.type === "horodatage" || c.type === "id_client") continue;
        if(TYPES_BOOLEEN.includes(c.type)) fd.append(c.slug, el.checked ? "1" : "0");
        else if(c.type === "choix_multiple") [...el.selectedOptions].forEach(o => fd.append(c.slug, o.value));
        else if(TYPES_FICHIER_LOCAL.includes(c.type)) { if(el.files[0]) fd.append(c.slug, el.files[0]); }
        else if(c.type === "relation"){
          const directEl = zone.querySelector("#champ_"+c.slug+"_direct");
          if(directEl && directEl.value !== directEl.dataset.baseline){
            // Édition directe de la valeur partagée (colonne bidirectionnelle)
            fd.append(c.slug, directEl.value);
            fd.append(c.slug+"__edition_directe", "1");
          } else {
            fd.append(c.slug, el.value); // _id stocké dans le hidden input (re-choix de ligne)
          }
        }
        else fd.append(c.slug, el.value);
      }
      await api(`/api/tables/${tableId}/lignes/${ligneId}/modifier/`, {method:"POST", body: fd});
    } else {
      const valeurs = {};
      for(const c of champs){
        const el = zone.querySelector("#champ_"+c.slug);
        if(!el) continue;
      if(c.type === "formule" || c.type === "constante" || c.type === "horodatage" || c.type === "id_client") continue;
        if(TYPES_BOOLEEN.includes(c.type)) valeurs[c.slug] = el.checked;
        else if(c.type === "choix_multiple") valeurs[c.slug] = [...el.selectedOptions].map(o => o.value);
        else if(c.type === "relation"){
          const directEl = zone.querySelector("#champ_"+c.slug+"_direct");
          if(directEl && directEl.value !== directEl.dataset.baseline){
            // Édition directe de la valeur partagée (colonne bidirectionnelle) :
            // on envoie la nouvelle valeur brute + un marqueur, plutôt que
            // l'_id de la ligne liée (voir api_modifier_ligne côté serveur).
            valeurs[c.slug] = directEl.value;
            valeurs[c.slug+"__edition_directe"] = "1";
          } else {
            valeurs[c.slug] = el.value; // _id stocké dans le hidden input (re-choix de ligne)
          }
        }
        else valeurs[c.slug] = el.value;
      }
      await api(`/api/tables/${tableId}/lignes/${ligneId}/modifier/`, {method:"POST", body: JSON.stringify(valeurs)});
    }
    notifierSucces(tj("engine/static/engine/js/index_1.js:5985:21"));
    document.getElementById("modal-ligne-fond").style.display = "none";
    afficherTable(slug);
  } catch(e) {
    // déjà notifié par api() (toast) ; on surligne aussi les champs fautifs
    // si le serveur les a identifiés (ex: type invalide).
    if(e && e.champsInvalides){
      for(const slug2 of Object.keys(e.champsInvalides)){
        const el = zone.querySelector("#champ_"+slug2);
        if(el) el.classList.add("champ-invalide");
      }
    }
  }
  finally { ev.target.textContent = tj("engine/static/engine/js/index_1.js:5998:38"); }
};

const DESCRIPTIONS_TYPE_TABLE = {
  normale: "",
  membres: tj("engine/static/engine/js/index_1.js:6003:13"),
};
function _rafraichirDescTypeTable(){
  const sel = document.getElementById("nouvelle-table-type");
  // La case « table de produits » n'a de sens que pour une table agents et clients.
  const zoneProduits = document.getElementById("nouvelle-table-produits-zone");
  if(zoneProduits){
    zoneProduits.style.display = sel.value === "membres" ? "block" : "none";
    if(sel.value !== "membres") document.getElementById("nouvelle-table-produits").checked = false;
  }
  document.getElementById("nouvelle-table-type-desc").textContent = DESCRIPTIONS_TYPE_TABLE[sel.value] || "";
}
document.getElementById("nouvelle-table-type").onchange = _rafraichirDescTypeTable;
document.getElementById("btn-nouvelle-table").onclick = () => {
  document.getElementById("nouvelle-table-nom").value = "";
  document.getElementById("nouvelle-table-echantillon").value = "5";
  document.getElementById("nouvelle-table-type").value = "normale";
  document.getElementById("nouvelle-table-formulaire").value = "standard";
  _rafraichirDescTypeTable();
  const zone = document.getElementById("nouveaux-champs");
  zone.innerHTML = ""; zone.appendChild(ligneChampVide());
  document.getElementById("modal-fond").classList.add("ouvert");
};
document.getElementById("btn-ajouter-champ").onclick = () => {
  document.getElementById("nouveaux-champs").appendChild(ligneChampVide());
};
document.getElementById("btn-annuler-table").onclick = () => document.getElementById("modal-fond").classList.remove("ouvert");
// ── Assistant visuel du champ calculé (constructeur pas-à-pas) ──────────
// Voir engine/assistant_formule.py:generer_code_formule pour l'équivalent
// serveur qui fait foi à la sauvegarde ; la fonction JS ci-dessous ne sert
// qu'à l'aperçu en direct, elle n'est jamais envoyée telle quelle au serveur.
const OPERATIONS_FORMULE = [
  ["addition", "Addition (+)"],
  ["soustraction", "Soustraction (−)"],
  ["multiplication", tj("engine/static/engine/js/index_1.js:op_multiplication")],
  ["division", tj("engine/static/engine/js/index_1.js:op_division")],
  ["moyenne", tj("engine/static/engine/js/index_1.js:6032:16")],
];
function _champsFormuleDisponibles(currentDiv){
  const conteneur = currentDiv.parentElement;
  if(!conteneur) return [];
  return [...conteneur.querySelectorAll(".ligne-structure")]
    .filter(li => li !== currentDiv)
    .map(li => {
      const nom = li.querySelector(".structure-nom")?.value.trim() || "";
      const slug = li.dataset.slug || nom.toLowerCase().replace(/\s+/g, "_");
      return { nom, slug };
    })
    .filter(c => c.nom && c.slug);
}
function _termesConstante(e){
  // Terme « constante » (nombre fixe, ex. 0.2 pour une TVA à 20 %) saisi dans une étape ; vide/invalide = aucun terme.
  const v = parseFloat((e.querySelector(".champ-formule-terme-constante")?.value || "").replace(",", "."));
  return Number.isFinite(v) ? [{type:"constante", valeur: v}] : [];
}
function _genererApercuFormule(etapes){
  const valides = etapes.filter(e => e.termes.length > 0);
  if(!valides.length) return tj("engine/static/engine/js/index_1.js:6053:31");
  const symboles = { addition:"+", soustraction:"−", multiplication:"×", division:"÷" };
  const lignes = [tj("engine/static/engine/js/index_1.js:6055:20")];
  let varPrecedent = null;
  valides.forEach((etape, i) => {
    const operandes = etape.termes.map(t => t.type === "precedent" ? varPrecedent : t.type === "constante" ? String(t.valeur) : `_v('${t.slug}')`).filter(Boolean);
    if(!operandes.length) operandes.push("0.0");
    const expr = etape.operation === "moyenne"
      ? `(${operandes.join(" + ")}) / ${operandes.length}`
      : operandes.join(` ${symboles[etape.operation] || "+"} `);
    const v = `r${i + 1}`;
    lignes.push(`    ${v} = ${expr}`);
    varPrecedent = v;
  });
  lignes.push(`    return ${varPrecedent}`);
  return lignes.join("\n");
}
function _initAssistantFormule(formuleConfig, ligneDiv, champ){
  const zoneEtapes   = formuleConfig.querySelector(".champ-formule-etapes");
  const btnAjouter    = formuleConfig.querySelector(".champ-formule-ajouter-etape");
  const apercu         = formuleConfig.querySelector(".champ-formule-apercu");
  const zoneAssistant = formuleConfig.querySelector(".champ-formule-assistant");
  const textarea       = formuleConfig.querySelector(".champ-formule-expression");
  const btnToggle       = formuleConfig.querySelector(".champ-formule-toggle-mode");

  function lireEtapes(){
    return [...zoneEtapes.querySelectorAll(".champ-formule-etape")].map(e => ({
      operation: e.querySelector(".champ-formule-etape-operation").value,
      termes: [
        ...(e.querySelector(".champ-formule-terme-precedent")?.checked ? [{type:"precedent"}] : []),
        ...[...e.querySelectorAll(".champ-formule-terme-champ:checked")].map(cb => ({type:"champ", slug: cb.value})),
        ..._termesConstante(e),
      ],
    }));
  }
  function rafraichirApercu(){ apercu.textContent = _genererApercuFormule(lireEtapes()); }

  function ajouterEtape(etapeInitiale){
    const numero = zoneEtapes.children.length + 1;
    const champsDispo = _champsFormuleDisponibles(ligneDiv);
    const termesInit = (etapeInitiale && etapeInitiale.termes) || [];
    const slugsCoches = new Set(termesInit.filter(t => t.type === "champ").map(t => t.slug));
    const precedentCoche = termesInit.some(t => t.type === "precedent");
    const constanteInit = termesInit.find(t => t.type === "constante");
    const el = document.createElement("div");
    el.className = "champ-formule-etape";
    el.style.cssText = "display:flex;flex-direction:column;gap:4px;padding:6px;background:var(--fond-sombre,#111);border:1px solid var(--border);border-radius:4px";
    el.innerHTML = `
      <div style="display:flex;align-items:center;gap:6px">
        <b style="font-size:11px;color:var(--muted);white-space:nowrap">${tj("engine/static/engine/js/index_1.js:6102:74")} ${numero}</b>
        <select class="champ-formule-etape-operation" style="flex:1">
          ${OPERATIONS_FORMULE.map(([v,l]) => `<option value="${v}" ${etapeInitiale?.operation===v?"selected":""}>${l}</option>`).join("")}
        </select>
        <button type="button" class="danger champ-formule-etape-supprimer" style="padding:2px 8px" title="${tj("engine/static/engine/js/index_1.js:6106:108")}">🗑</button>
      </div>
      <div class="champ-formule-etape-champs" style="display:flex;flex-wrap:wrap;gap:8px;font-size:12px">
        ${zoneEtapes.children.length > 0 ? `<label class="chk"><input type="checkbox" class="champ-formule-terme-precedent" ${precedentCoche?"checked":""}> ${tj("engine/static/engine/js/index_1.js:6109:158")}</label>` : ""}
        ${champsDispo.length
          ? champsDispo.map(c => `<label class="chk"><input type="checkbox" class="champ-formule-terme-champ" value="${escapeHtml(c.slug)}" ${slugsCoches.has(c.slug)?"checked":""}> ${escapeHtml(c.nom)}</label>`).join("")
          : `<span style="color:var(--muted)">${tj("engine/static/engine/js/index_1.js:6112:48")}</span>`}
        <label class="chk">${tj("engine/static/engine/js/index_1.js:6113:29")} <input type="text" inputmode="decimal" class="champ-formule-terme-constante" value="${constanteInit ? escapeHtml(String(constanteInit.valeur)) : ""}" placeholder="ex. 0.2" style="width:70px"></label>
      </div>`;
    el.querySelector(".champ-formule-etape-supprimer").onclick = () => {
      el.remove();
      // Renumérote les étapes affichées et retire les "résultat précédent"
      // orphelins si l'étape 1 vient d'être supprimée.
      [...zoneEtapes.children].forEach((e, i) => { e.querySelector("b").textContent = tj("engine/static/engine/js/index_1.js:6119:88", {v1: i + 1}); });
      if(zoneEtapes.children[0]){
        zoneEtapes.children[0].querySelector(".champ-formule-terme-precedent")?.closest("label")?.remove();
      }
      rafraichirApercu();
    };
    el.querySelectorAll("select, input").forEach(inp => { inp.addEventListener("change", rafraichirApercu); inp.addEventListener("input", rafraichirApercu); });
    zoneEtapes.appendChild(el);
    rafraichirApercu();
  }
  btnAjouter.onclick = () => ajouterEtape();

  // ── Bascule assistant visuel / code libre ───────────────────────────
  function majAffichageMode(){
    const modeCode = ligneDiv.dataset.formuleMode === "code";
    zoneAssistant.style.display = modeCode ? "none" : "flex";
    textarea.style.display = modeCode ? "block" : "none";
    btnToggle.textContent = modeCode ? tj("engine/static/engine/js/index_1.js:6136:41") : tj("engine/static/engine/js/index_1.js:6136:80");
  }
  btnToggle.onclick = () => {
    if(ligneDiv.dataset.formuleMode !== "code" && !textarea.value.trim()){
      // On bascule vers le code : préremplit avec l'équivalent de l'assistant.
      textarea.value = _genererApercuFormule(lireEtapes());
    }
    ligneDiv.dataset.formuleMode = ligneDiv.dataset.formuleMode === "code" ? "assistant" : "code";
    majAffichageMode();
  };

  // ── État initial : étapes existantes (mode assistant), sinon code libre
  // si une formule_expression était déjà écrite à la main, sinon une étape
  // vide en mode assistant par défaut pour un champ tout neuf.
  const etapesInitiales = Array.isArray(champ.formule_etapes_json) ? champ.formule_etapes_json : [];
  if(etapesInitiales.length){
    ligneDiv.dataset.formuleMode = "assistant";
    etapesInitiales.forEach(e => ajouterEtape(e));
  } else if((champ.formule_expression || "").trim()){
    ligneDiv.dataset.formuleMode = "code";
  } else {
    ligneDiv.dataset.formuleMode = "assistant";
    ajouterEtape();
  }
  majAffichageMode();
}
function ligneStructureChamp(champ){
  champ = champ || {nom:"", slug:"", type:"texte", obligatoire:true, options:"", relation_vers_id:null, relation_champ_affichage:"", relation_colonne_source:"", relation_bidirectionnelle:false, groupe_partage_id:null, partage_membres:[]};
  const div = document.createElement("div"); div.className = "ligne-structure";
  div.dataset.slug = champ.slug || "";
  div.dataset.groupePartageId = champ.groupe_partage_id || "";
  div.dataset.couleur = champ.couleur || "";
  const isRelation = champ.type === "relation" || champ.type === "colonne";
  const dejaPartage = !!champ.groupe_partage_id;
  const membresTxt = (champ.partage_membres||[]).map(m => `${escapeHtml(m.table_nom)}.${escapeHtml(m.champ_nom)}`).join(", ");
  div.innerHTML = `
    <input type="text" class="structure-nom" placeholder="${tj("engine/static/engine/js/index_1.js:a:6184:58")}" value="${escapeHtml(champ.nom)}">
    <select class="structure-type">${TYPES_CHAMPS.map(t=>`<option value="${t}" ${t===champ.type?"selected":""}>${t}</option>`).join("")}</select>
    <label class="chk"><input type="checkbox" class="structure-obligatoire" ${champ.obligatoire?"checked":""}> ${tj("engine/static/engine/js/index_1.js:6174:113")}</label>
    <button type="button" class="structure-couleur-btn" title="${tj("engine/static/engine/js/index_1.js:6175:65")}" style="padding:6px 8px">🎨</button>
    <button type="button" class="structure-partage-btn" title="${tj("engine/static/engine/js/index_1.js:6176:65")}" style="padding:6px 8px">${dejaPartage?tj("engine/static/engine/js/index_1.js:6176:155"):tj("engine/static/engine/js/index_1.js:6176:168")}</button>
    <input type="number" min="1" step="1" class="champ-taille-max" placeholder="${tj("engine/static/engine/js/index_1.js:taille_max_mo")}" title="${tj("engine/static/engine/js/index_1.js:taille_max_mo")}" value="${champ.taille_max_mo||""}" style="display:none;width:150px">
    <button class="danger structure-supprimer" title="${tj("engine/static/engine/js/index_1.js:6177:56")}" style="padding:6px 8px">🗑</button>
    <div class="champ-relation-config" style="display:${isRelation?"flex":"none"}">
      <select class="champ-relation-table-cible" style="flex:2;min-width:140px">
        <option value="">${tj("engine/static/engine/js/index_1.js:6180:27")}</option>
        ${tables.map(t=>`<option value="${t.id}" ${String(t.id)===String(champ.relation_vers_id)?"selected":""}>${escapeHtml(t.nom)}</option>`).join("")}
      </select>
      <select class="champ-relation-champ-aff" style="flex:2;min-width:130px" disabled>
        <option value="">${tj("engine/static/engine/js/index_1.js:6184:27")}</option>
      </select>
      <select class="champ-relation-col-src" style="flex:2;min-width:130px" disabled>
        <option value="">${tj("engine/static/engine/js/index_1.js:6187:27")}</option>
      </select>
      <label class="chk champ-relation-bidir-lbl" title="${tj("engine/static/engine/js/index_1.js:6189:59")}">
        <input type="checkbox" class="champ-relation-bidirectionnelle" ${champ.relation_bidirectionnelle?"checked":""}> ${tj("engine/static/engine/js/index_1.js:6190:122")}
      </label>
    </div>
    <div class="champ-formule-config" style="display:${champ.type==="formule"?"flex":"none"};grid-column:1/-1;flex-direction:column;gap:6px;padding:8px;background:var(--fond-noir,#000000);border:1px solid var(--border);border-radius:6px">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
        <span style="font-size:11px;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:6195:58")}</span>
        <button type="button" class="champ-formule-toggle-mode" style="padding:4px 10px;font-size:11px;white-space:nowrap">${tj("engine/static/engine/js/index_1.js:6196:125")}</button>
      </div>
      <div class="champ-formule-assistant" style="display:flex;flex-direction:column;gap:6px">
        <div class="champ-formule-etapes" style="display:flex;flex-direction:column;gap:6px"></div>
        <button type="button" class="champ-formule-ajouter-etape" style="align-self:flex-start;padding:4px 10px;font-size:12px">${tj("engine/static/engine/js/index_1.js:6200:130")}</button>
        <pre class="champ-formule-apercu" style="margin:0;font-family:monospace;font-size:11px;color:var(--muted);white-space:pre-wrap;background:var(--fond-tres-sombre,#0a0a0a);padding:6px;border-radius:4px;max-height:140px;overflow:auto"></pre>
      </div>
      <textarea class="champ-formule-expression" rows="4" style="display:none;font-family:monospace;font-size:12px" placeholder="def formule(ligne):
    return ligne.get('prix', 0) * ligne.get('quantite', 0)">${escapeHtml(champ.formule_expression || "")}</textarea>
    </div>
    <div class="champ-constante-config" style="display:${champ.type==="constante"?"flex":"none"};grid-column:1/-1;gap:8px;align-items:center;flex-wrap:wrap;padding:8px;background:var(--fond-noir,#000000);border:1px solid var(--border);border-radius:6px">
      <span style="font-size:11px;color:var(--muted)">${tj("engine/static/engine/js/index_1.js:6207:56")}</span>
      <input type="text" inputmode="decimal" class="structure-constante-valeur" value="${champ.type==="constante" ? escapeHtml(String(champ.options || "")) : ""}" placeholder="ex. 0.2" style="width:90px">
    </div>
    <div class="champ-partage-config" style="display:none;grid-column:1/-1;gap:6px;flex-direction:column;padding:8px;background:var(--fond-noir,#000000);border:1px solid var(--border);border-radius:6px">
      <span class="champ-partage-info" style="font-size:12px;color:var(--muted)">${dejaPartage
        ? tj("engine/static/engine/js/index_1.js:6212:12", {v1: membresTxt || "—"})
        : tj("engine/static/engine/js/index_1.js:6213:12")}</span>
      <div class="champ-partage-cibles" style="display:flex;flex-direction:column;gap:6px"></div>
      <button type="button" class="champ-partage-ajouter-cible" style="align-self:flex-start;padding:4px 10px;font-size:12px">${tj("engine/static/engine/js/index_1.js:6215:128")}</button>
    </div>`;
  div.querySelector(".structure-supprimer").onclick = () => div.remove();
  if(champ.type === "colonne") div.querySelector(".champ-relation-bidir-lbl").style.display = "none";
  const typeSelect = div.querySelector(".structure-type");
  const relConfig  = div.querySelector(".champ-relation-config");
  const tableSel   = div.querySelector(".champ-relation-table-cible");
  const affSel     = div.querySelector(".champ-relation-champ-aff");
  const srcSel     = div.querySelector(".champ-relation-col-src");
  const formuleConfig = div.querySelector(".champ-formule-config");
  const tailleInp = div.querySelector(".champ-taille-max");
  const majTaille = () => { tailleInp.style.display = TYPES_FICHIER_LOCAL.includes(typeSelect.value) ? "" : "none"; };
  majTaille();
  typeSelect.onchange = () => {
    majTaille();
    _majConfigRelation(relConfig, typeSelect.value, tableSel, affSel, srcSel);
    formuleConfig.style.display = typeSelect.value === "formule" ? "flex" : "none";
    div.querySelector(".champ-constante-config").style.display = typeSelect.value === "constante" ? "flex" : "none";
  };
  _initAssistantFormule(formuleConfig, div, champ);
  tableSel.onchange = () => _chargerChampsRelation(tableSel.value, affSel, srcSel, "", "");
  // Pré-charger si édition d'un champ relation existant
  if(isRelation && champ.relation_vers_id){
    _chargerChampsRelation(champ.relation_vers_id, affSel, srcSel,
      champ.relation_champ_affichage||"", champ.relation_colonne_source||"");
  }

  // ── Bouton "Rendre partagée" (colonne partagée / GroupePartage) ──
  const partageBtn    = div.querySelector(".structure-partage-btn");
  const partageConfig = div.querySelector(".champ-partage-config");
  const partageCibles     = div.querySelector(".champ-partage-cibles");
  const partageAjouterBtn = div.querySelector(".champ-partage-ajouter-cible");
  let partageActive = dejaPartage;
  const majBoutonPartage = () => {
    partageBtn.textContent = partageActive ? tj("engine/static/engine/js/index_1.js:6245:47") : tj("engine/static/engine/js/index_1.js:6245:62");
    partageBtn.style.background = partageActive ? "var(--accent)" : "";
    partageConfig.style.display = partageActive ? "flex" : "none";
  };
  majBoutonPartage();
  partageBtn.onclick = () => {
    partageActive = !partageActive;
    if(!partageActive){ div.dataset.groupePartageId = ""; }
    else if(partageCibles.children.length === 0){ partageCibles.appendChild(ligneCiblePartage()); }
    majBoutonPartage();
  };
  partageAjouterBtn.onclick = () => partageCibles.appendChild(ligneCiblePartage());

  return div;
}
function ouvrirModalStructure(table){
  document.getElementById("modal-structure-table-nom").textContent = table.nom;
  const noteGroupe = document.getElementById("structure-groupe-note");  // table d'un groupe : structure commune à tout le groupe
  noteGroupe.textContent = table.groupe_id ? tj("engine/static/engine/js/index_1.js:6263:47") + table.groupe_nom + tj("engine/static/engine/js/index_1.js:6263:107") : "";
  noteGroupe.style.display = table.groupe_id ? "block" : "none";
  const zone = document.getElementById("structure-champs");
  zone.innerHTML = "";
  for(const c of table.champs){ const l = ligneStructureChamp(c); zone.appendChild(l); _teinterLigneStructure(l); }
  document.getElementById("modal-structure-fond").style.display = "flex";
  document.getElementById("modal-structure-fond").dataset.tableId = table.id;
}
function _teinterLigneStructure(l){ const n = l.querySelector(".structure-nom"), c = l.dataset.couleur; n.style.background = c || ""; n.style.color = c ? _texteSurCouleur(c) : ""; }
document.getElementById("structure-champs").addEventListener("click", async (e) => {   // 🎨 d'une ligne : valable aussi pour une colonne qu'on vient d'ajouter
  const b = e.target.closest(".structure-couleur-btn"); if(!b) return;
  const l = b.closest(".ligne-structure");
  const c = await GYCouleur.choisir({titre: tj("engine/static/engine/js/index_1.js:6275:46", {v1: l.querySelector(".structure-nom").value.trim()}), valeur: l.dataset.couleur || ""});
  if(c === null) return;
  l.dataset.couleur = c; _teinterLigneStructure(l);
});
document.getElementById("btn-ajouter-champ-structure").onclick = () => {
  try {
    const ligne = ligneStructureChamp();
    document.getElementById("structure-champs").appendChild(ligne);
    ligne.scrollIntoView({block: "center", behavior: "smooth"});  // la ligne s'ajoute en bas d'une liste longue : la montrer
    ligne.querySelector(".structure-nom").focus();
    notifier(tj("engine/static/engine/js/index_1.js:6285:15"));
  } catch (e) { console.error(e); notifier(tj("engine/static/engine/js/index_1.js:6286:45") + e.message, "erreur"); }
};
document.getElementById("btn-annuler-structure").onclick = () => {
  document.getElementById("modal-structure-fond").style.display = "none";
};
document.getElementById("btn-enregistrer-structure").onclick = async (ev) => {
  const tableId = document.getElementById("modal-structure-fond").dataset.tableId;
  const lignes = [...document.querySelectorAll("#structure-champs .ligne-structure")];
  const champs = lignes.map(l => ({
    nom: l.querySelector(".structure-nom").value.trim(),
    slug: l.dataset.slug || undefined,
    type: l.querySelector(".structure-type").value,
    // Constante : la valeur (ex. 0.2) part dans `options` ; pour les autres types on n'envoie rien (options inchangées).
    options: l.querySelector(".structure-type").value === "constante"
      ? (l.querySelector(".structure-constante-valeur")?.value || "").trim() : undefined,
    obligatoire: l.querySelector(".structure-obligatoire").checked,
    taille_max_mo: l.querySelector(".champ-taille-max")?.value || "",   // vide = aucune limite (efface un ancien réglage)
    couleur: l.dataset.couleur || "",
    formule_expression: l.querySelector(".champ-formule-expression")?.value || "",
    // Mode assistant visuel : les étapes font foi côté serveur, qui regénère
    // formule_expression à partir d'elles (voir assistant_formule.py) — en
    // mode code libre on envoie null pour ne PAS écraser le code écrit à la
    // main avec une régénération, et pour que la structure retombe bien en
    // mode "code libre" à la prochaine ouverture (voir _initAssistantFormule).
    formule_etapes_json: l.dataset.formuleMode === "assistant"
      ? [...l.querySelectorAll(".champ-formule-etape")].map(e => ({
          operation: e.querySelector(".champ-formule-etape-operation").value,
          termes: [
            ...(e.querySelector(".champ-formule-terme-precedent")?.checked ? [{type:"precedent"}] : []),
            ...[...e.querySelectorAll(".champ-formule-terme-champ:checked")].map(cb => ({type:"champ", slug: cb.value})),
            ..._termesConstante(e),
          ],
        }))
      : null,
    relation_vers_id: l.querySelector(".champ-relation-table-cible")?.value || null,
    relation_champ_affichage: l.querySelector(".champ-relation-champ-aff")?.value || "",
    relation_colonne_source: l.querySelector(".champ-relation-col-src")?.value || "",
    relation_bidirectionnelle: l.querySelector(".champ-relation-bidirectionnelle")?.checked || false,
    // Colonne partagée (GroupePartage) : si l'utilisateur a choisi une ou
    // plusieurs nouvelles tables/colonnes cibles, on les envoie toutes
    // (le backend crée ou rejoint/fusionne le(s) groupe(s) — permet un
    // partage en GROUPE, pas seulement en paire). Sinon, si la colonne
    // était déjà partagée et que le bouton "Rendre partagée" est resté
    // actif, on renvoie juste son groupe actuel pour ne pas la retirer par
    // erreur. Si le bouton a été désactivé, rien n'est envoyé => la
    // colonne est retirée de son groupe.
    partage_cibles: [...l.querySelectorAll(".champ-partage-cible-ligne")]
      .map(c => ({
        table_id: c.querySelector(".champ-partage-table-cible")?.value || "",
        slug: c.querySelector(".champ-partage-colonne-cible")?.value || "",
      }))
      .filter(c => c.table_id && c.slug),
    groupe_partage_id: l.dataset.groupePartageId || "",
  })).filter(c => c.nom);

  if(champs.length === 0){
    notifierErreur(tj("engine/static/engine/js/index_1.js:6341:21"));
    return;
  }

  ev.target.textContent = "…"; ev.target.disabled = true;
  try {
    const res = await api(`/api/tables/${tableId}/restructurer/`, {method:"POST", body: JSON.stringify({champs})});
    notifierSucces(res.table.groupe_id ? tj("engine/static/engine/js/index_1.js:6348:43") + res.table.groupe_nom + tj("engine/static/engine/js/index_1.js:6348:93") : tj("engine/static/engine/js/index_1.js:6348:113"));
    document.getElementById("modal-structure-fond").style.display = "none";
    await rafraichirTables();
    if(tableActive === res.table.slug){
      if(modeVue === "table") afficherTable(res.table.slug); else afficherTableauBord(res.table.slug);
    }
  } catch(e) {
    // EXCEPTION 2026-09-24 (voir obligation_pour_les_ia_avant_de_commencer.txt) : limite de
    // colonnes atteinte — proposer une table liée pour les colonnes en trop, plutôt que de
    // laisser l'utilisateur bloqué sans solution.
    if(e && e.peutEtendre && e.plafondColonnes && await GY.confirmer(tj("engine/static/engine/js/index_1.js:6358:60", {v1: e.plafondColonnes}))){
      try{
        const enTrop = champs.slice(e.plafondColonnes);
        await api(`/api/tables/${tableId}/restructurer/`, {method:"POST", body: JSON.stringify({champs: champs.slice(0, e.plafondColonnes)})});
        const ext = await api(`/api/tables/${tableId}/etendre-colonnes/`, {method:"POST"});
        await api(`/api/tables/${ext.table.id}/restructurer/`, {method:"POST", body: JSON.stringify({champs: enTrop})});
        notifierSucces(tj("engine/static/engine/js/index_1.js:6364:25", {v1: ext.table.nom}));
        document.getElementById("modal-structure-fond").style.display = "none";
        await rafraichirTables();
      } catch(e2) { /* déjà notifié par api() */ }
    }
  }
  finally { ev.target.textContent = tj("engine/static/engine/js/index_1.js:6370:38"); ev.target.disabled = false; }
};
// Onglets du modal Config IA PAR TABLE (canal analyse / canal document) —
// même principe que cfgActiverOnglet du modal global, mais pour ce modal-ci.
function promptActiverCanal(canal){
  const panelAnalyse = document.getElementById("modal-prompt-panel-analyse");
  const panelDoc     = document.getElementById("modal-prompt-panel-document");
  const tabAnalyse   = document.getElementById("modal-prompt-tab-analyse");
  const tabDoc       = document.getElementById("modal-prompt-tab-document");
  if(canal === "document"){
    panelAnalyse.style.display = "none";
    panelDoc.style.display     = "block";
    tabDoc.style.background     = "var(--accent)"; tabDoc.style.color     = gyCouleur("--texte-sombre","#fff");
    tabAnalyse.style.background = "var(--panel)";  tabAnalyse.style.color = "var(--muted)";
  } else {
    panelAnalyse.style.display = "block";
    panelDoc.style.display     = "none";
    tabAnalyse.style.background = "var(--accent)"; tabAnalyse.style.color = gyCouleur("--texte-sombre","#fff");
    tabDoc.style.background     = "var(--panel)";  tabDoc.style.color     = "var(--muted)";
  }
}
function ouvrirModalPrompt(table){
  document.getElementById("modal-prompt-table-nom").textContent = table.nom;
  document.getElementById("modal-prompt-texte").value = table.prompt_rapport || "";
  document.getElementById("modal-prompt-texte-document").value = table.prompt_rapport_document || "";
  document.getElementById("modal-prompt-timeout-execution").value = table.timeout_execution_secondes ?? 30;
  document.getElementById("modal-prompt-timeout-inference").value = table.timeout_inference_secondes ?? 120;
  document.getElementById("modal-prompt-fond").style.display = "flex";
  document.getElementById("modal-prompt-fond").dataset.tableId = table.id;
  promptActiverCanal("analyse");
}
document.getElementById("btn-annuler-prompt").onclick = () => {
  document.getElementById("modal-prompt-fond").style.display = "none";
};

document.getElementById("btn-enregistrer-prompt").onclick = async (ev) => {
  const tableId = document.getElementById("modal-prompt-fond").dataset.tableId;
  const prompt_rapport = document.getElementById("modal-prompt-texte").value;
  const prompt_rapport_document = document.getElementById("modal-prompt-texte-document").value;
  const timeout_execution_secondes = parseInt(document.getElementById("modal-prompt-timeout-execution").value, 10) || 30;
  const timeout_inference_secondes = parseInt(document.getElementById("modal-prompt-timeout-inference").value, 10) || 120;
  ev.target.textContent = "…";
  try {
    await api(`/api/tables/${tableId}/modifier/`, {method:"POST", body: JSON.stringify({
      prompt_rapport, prompt_rapport_document, timeout_execution_secondes, timeout_inference_secondes,
    })});
    notifierSucces(tj("engine/static/engine/js/index_1.js:6416:21"));
    document.getElementById("modal-prompt-fond").style.display = "none";
    await rafraichirTables();
    if(tableActive) afficherTable(tableActive);
  } catch(e) { /* déjà notifié par api() */ }
  finally { ev.target.textContent = tj("engine/static/engine/js/index_1.js:6421:38"); }
};

document.getElementById("btn-creer-table").onclick = async (ev) => {
  const btn = ev.currentTarget, texteBtn = btn.textContent;
  if(btn.disabled) return;   // un 2e clic pendant la création ferait une erreur « nom déjà pris » alors que la table existe
  const nom = document.getElementById("nouvelle-table-nom").value.trim();
  if(!nom){ notifierErreur(tj("engine/static/engine/js/index_1.js:6426:29")); return; }
  btn.disabled = true; btn.textContent = tj("engine/static/engine/js/index_1.js:6772:43");
  const champs = [...document.querySelectorAll("#nouveaux-champs .ligne-champ-modal")].map(l => ({
    nom: l.querySelector(".champ-nom").value.trim(),
    type: l.querySelector(".champ-type").value,
    options: l.querySelector(".champ-options").value.trim(),
    obligatoire: l.querySelector(".champ-obligatoire").checked,
    taille_max_mo: l.querySelector(".champ-taille-max")?.value || "",   // vide = aucune limite
    relation_vers_id: l.querySelector(".champ-relation-table-cible")?.value || null,
    relation_champ_affichage: l.querySelector(".champ-relation-champ-aff")?.value || "",
    relation_colonne_source: l.querySelector(".champ-relation-col-src")?.value || "",
    relation_bidirectionnelle: l.querySelector(".champ-relation-bidirectionnelle")?.checked || false,
  })).filter(c => c.nom);

  const typeTable = document.getElementById("nouvelle-table-type").value;
  const typeStockage = document.getElementById("nouvelle-table-stockage")?.value === "nosql" ? "nosql" : "sql";
  try {
    const res = await api("/api/tables/", {method:"POST", body: JSON.stringify({
      nom, champs, taille_echantillon: parseInt(document.getElementById("nouvelle-table-echantillon").value, 10) || 5,
      est_table_membres: typeTable === "membres",
      est_table_produits: typeTable === "membres" && !!document.getElementById("nouvelle-table-produits")?.checked,
      type_stockage: typeStockage,
    })});
    notifierSucces(tj("engine/static/engine/js/index_1.js:6446:21", {v1: res.table.nom}));
    document.getElementById("modal-fond").classList.remove("ouvert");
    await rafraichirTables();
    modeVue="dashboard"; tableActive=res.table.slug; afficherTableauBord(res.table.slug); rafraichirTables();
    // Nature « Conditionnel » choisie à la construction : on enchaîne sur l'éditeur de sections (la table, elle, est déjà créée telle quelle).
    if(document.getElementById("nouvelle-table-formulaire")?.value === "conditionnel") window.gyFormulaireConditionnel.ouvrir(res.table, "conditionnel");
  } catch(e) {
    // EXCEPTION 2026-09-24 (voir obligation_pour_les_ia_avant_de_commencer.txt) : limite de
    // colonnes atteinte à la création — créer la table avec les premières colonnes, puis
    // l'étendre pour les colonnes restantes, plutôt que de refuser la création entière.
    if(e && e.peutEtendre && e.plafondColonnes && await GY.confirmer(tj("engine/static/engine/js/index_1.js:6454:60", {v1: e.plafondColonnes, v2: e.plafondColonnes}))){
      try{
        const enTrop = champs.slice(e.plafondColonnes);
        const res2 = await api("/api/tables/", {method:"POST", body: JSON.stringify({
          nom, champs: champs.slice(0, e.plafondColonnes),
          taille_echantillon: parseInt(document.getElementById("nouvelle-table-echantillon").value, 10) || 5,
          est_table_membres: typeTable === "membres",
      est_table_produits: typeTable === "membres" && !!document.getElementById("nouvelle-table-produits")?.checked,
        })});
        const ext = await api(`/api/tables/${res2.table.id}/etendre-colonnes/`, {method:"POST"});
        await api(`/api/tables/${ext.table.id}/restructurer/`, {method:"POST", body: JSON.stringify({champs: enTrop})});
        notifierSucces(tj("engine/static/engine/js/index_1.js:6464:25", {v1: res2.table.nom, v2: ext.table.nom}));
        document.getElementById("modal-fond").classList.remove("ouvert");
        await rafraichirTables();
        modeVue="dashboard"; tableActive=res2.table.slug; afficherTableauBord(res2.table.slug); rafraichirTables();
      } catch(e2) { /* déjà notifié par api() */ }
    }
  } finally { btn.disabled = false; btn.textContent = texteBtn; }
};
// Échappe aussi " et ' : l'ancienne version (div.textContent → innerHTML) laissait passer les guillemets,
// donc une valeur placée dans un attribut (value="…", title="…", alt="…") pouvait en sortir (XSS).
function escapeHtml(s){ return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]); }
// Rempli par chargerProfil() avant que initApp() ne décide quoi afficher.
async function chargerProfil(){
  try {
    const p = await api("/api/profile/");
    const initiales = (p.first_name||"")[0] + (p.last_name||"")[0];
    const avatarEl = document.getElementById("user-avatar");
    if(!avatarEl){
      /* plus d'avatar dans le bandeau */
    } else if(p.photo_profil){
      avatarEl.classList.add("avatar-photo");
      avatarEl.innerHTML = `<img src="${p.photo_profil}" alt="${tj("engine/static/engine/js/index_1.js:6834:63")}">`;
    } else {
      avatarEl.textContent = initiales || p.username[0].toUpperCase();
    }
    // Un client se reconnaît à son RÔLE (lecteur), pas à un préfixe d'identifiant : le préfixe testé
    // ici auparavant ne correspondait pas à celui créé par le serveur (« client_<CODE> »).
    const estClient = !!p.est_lecteur;
    const nomAffiche = (p.first_name && p.last_name)
      ? `${p.first_name} ${p.last_name}`
      : estClient ? tj("engine/static/engine/js/index_1.js:6492:22") : p.username;
    { const _un = document.getElementById("user-nom"); if(_un) _un.textContent = nomAffiche; }
    const avatarTxt = estClient ? "C" : (initiales || p.username[0].toUpperCase());
    if(avatarEl && !p.photo_profil) avatarEl.textContent = avatarTxt;
    // Chaque bouton réservé suit SON droit de délégation (p.droits : liste des droits effectifs ; l'administrateur les a tous).
    const droits = new Set(p.droits || []);
    if(droits.has("employes")) document.getElementById("btn-employes").style.display = "block";
    if(droits.has("decisions")) document.getElementById("btn-recommandations").style.display = "block";
    if(droits.has("usabilite")) document.getElementById("btn-usabilite").style.display = "block";
    if(droits.has("cloture_cycle")) document.getElementById("btn-cloture-cycle").style.display = "block";
    if(droits.has("reglages_entreprise")){
      document.getElementById("div-limite-lignes-globale").style.display = "flex";
      document.getElementById("div-limite-colonnes-globale").style.display = "flex";
    }
    if(!p.est_lecteur) document.getElementById("btn-config-documents").style.display = "block";
    if(!p.est_lecteur && p.documents_administratifs_autorise !== false) document.getElementById("btn-documents-administratifs").style.display = "block";
    if(["marque", "couleurs", "identification"].some(d => droits.has(d))) document.getElementById("btn-personnalisation").style.display = "block";
    if(!p.est_lecteur) document.getElementById("btn-paiement-config").style.display = "block";
    if(!p.est_lecteur) document.getElementById("btn-wallets-clients").style.display = "block";
    if(p.finances_autorise) document.getElementById("btn-finances").style.display = "block";   // administrateur, ou agent à qui la vue « finances » est accordée
    if(p.est_super_admin) document.getElementById("btn-siem").style.display = "block";   // le SIEM n'est ouvert qu'au super-administrateur (la page refuse tout autre)
    if(p.est_super_admin) ["btn-sauvegarde", "btn-restauration", "btn-entreprises-plateforme"].forEach(id => document.getElementById(id).style.display = "block");   // pages de la plateforme : super-administrateur seulement (les pages refusent tout autre)
    if(!p.est_lecteur) document.getElementById("btn-notifications").style.display = "block";
    if(!p.est_lecteur) document.getElementById("btn-messagerie").style.display = "block";
    if(!p.est_lecteur) document.getElementById("btn-agenda").style.display = "block";
    if(!p.est_lecteur) document.getElementById("btn-publicites").style.display = "block";
    if(!p.est_lecteur) document.getElementById("btn-chatbot").style.display = "block";
    if(!p.est_lecteur && typeof demarrerClocheNotifications === "function") demarrerClocheNotifications();   // cloche à côté du profil
    return p;
  } catch(e) { return null; }
}
document.getElementById("btn-employes").onclick = () => { window.location.href = "/employes/"; };
document.getElementById("btn-recommandations").onclick = () => { window.location.href = "/recommandations/"; };
document.getElementById("btn-config-documents").onclick = () => { window.location.href = "/configuration-documents/"; };
document.getElementById("btn-documents-administratifs").onclick = () => { window.location.href = "/documents-administratifs/"; };
document.getElementById("btn-personnalisation").onclick = () => { window.location.href = "/personnalisation/"; };
document.getElementById("btn-paiement-config").onclick = () => { window.location.href = "/paiement-config/"; };
document.getElementById("btn-wallets-clients").onclick = () => { window.location.href = "/wallets-clients/"; };
document.getElementById("btn-finances").onclick = () => { window.location.href = "/finances/"; };
document.getElementById("btn-siem").onclick = () => { window.location.href = "/siem/"; };
document.getElementById("btn-sauvegarde").onclick = () => { window.location.href = "/plateforme/sauvegarde/#sauvegarder"; };
document.getElementById("btn-restauration").onclick = () => { window.location.href = "/plateforme/sauvegarde/#restaurer"; };
document.getElementById("btn-entreprises-plateforme").onclick = () => { window.location.href = "/plateforme/entreprises/"; };
document.getElementById("btn-notifications").onclick = () => { window.location.href = "/notifications/"; };
document.getElementById("btn-messagerie").onclick = () => { window.location.href = "/messagerie/"; };
document.getElementById("btn-agenda").onclick = () => { window.location.href = "/agenda/"; };
document.getElementById("btn-flux-media").onclick = () => { window.location.href = "/flux/"; };
document.getElementById("btn-publicites").onclick = () => { window.location.href = "/publicites/"; };
document.getElementById("btn-chatbot").onclick = () => { window.location.href = "/chatbot/"; };
document.getElementById("btn-usabilite").onclick = () => { window.location.href = "/usabilite/"; };
document.getElementById("btn-taches-sidebar").onclick = () => { window.location.href = "/taches/"; };
// Lit le slug d'une "vue dédiée" depuis l'URL :
//   /tables/<slug>/       → le slug est dans le path
//   /?table=<slug>        → le slug est en query (fallback pratique)
function lireSlugDepuisUrl(){
  const chemin = location.pathname.match(/^\/tables\/([a-z0-9_\-]+)\/?$/i);
  if(chemin) return chemin[1];
  const params = new URLSearchParams(location.search);
  return params.get("table");
}
(async function init(){
  const profil = await chargerProfil();
  // Mode client (lecteur) : interface simplifiée, uniquement les formulaires
  // exposés — on ne doit JAMAIS charger/afficher le tableau de bord ni la
  // vue table, même brièvement.
  if(profil && profil.est_lecteur){
    activerModeClient(profil);
    return;
  }

  await rafraichirTables();
  chargerLimiteLignesGlobale();
  chargerLimiteColonnesGlobale();
  let slugDemande = lireSlugDepuisUrl();
  const demandeParUrl = !!slugDemande;
  let vueDemandee = new URLSearchParams(location.search).get("vue");
  if(!slugDemande){
    const memo = lireContexteAccueil();   // retour sur l'accueil : on reprend là où la personne en était
    if(memo){ slugDemande = memo.slug; vueDemandee = {table: "donnees", documents: "documents", calcul: "calcul", dashboard: "dashboard"}[memo.vue] || "donnees"; }
  }
  if(slugDemande){
    // Vue dédiée : on cherche la table demandée ; si elle n'existe pas
    // (ou n'appartient pas à l'entreprise), on retombe sur la première
    // table dispo avec un toast d'erreur explicite.
    if(!tables.some(t => t.slug === slugDemande) && /^systeme_/.test(slugDemande)){
      try{
        const ds = await api("/api/tables/?systeme=1");
        const ts = (ds.tables || []).find(t => t.slug === slugDemande);
        if(ts){ window._tableSystemeDemandee = ts; tables.push(ts); }
      }catch(e){ /* déjà notifié par api() */ }
    }
    const tableDemandee = tables.find(t => t.slug === slugDemande);
    if(tableDemandee){
      tableActive = tableDemandee.slug;
      const autorisee = !Array.isArray(window.VUES_AUTORISEES) || !window.VUES_AUTORISEES.length || window.VUES_AUTORISEES.includes(vueDemandee);
      if(autorisee && vueDemandee === "donnees"){ modeVue = "table"; await afficherTable(tableActive); }
      else if(autorisee && vueDemandee === "documents"){ modeVue = "documents"; await afficherDocuments(tableActive); }
      else if(autorisee && vueDemandee === "calcul"){ modeVue = "calcul"; await afficherCalcul(tableActive); }
      else if(autorisee && vueDemandee === "dashboard"){ modeVue = "dashboard"; await afficherTableauBord(tableActive); }
      else if(!Array.isArray(window.VUES_AUTORISEES) || !window.VUES_AUTORISEES.length || window.VUES_AUTORISEES.includes("donnees")){ modeVue = "table"; await afficherTable(tableActive); }   // vue d'ouverture : le gestionnaire de données (et non le générateur de rapport IA)
      else { modeVue = "dashboard"; await afficherTableauBord(tableActive); }   // vues « donnees » non autorisées : comme avant
      rafraichirTables();
      return;
    }
    if(demandeParUrl) notifierErreur(tj("engine/static/engine/js/index_1.js:6562:21", {v1: slugDemande}));
  }
  if(tables.length > 0){
    tableActive = tables[0].slug;
    if(!Array.isArray(window.VUES_AUTORISEES) || !window.VUES_AUTORISEES.length || window.VUES_AUTORISEES.includes("donnees")){ modeVue = "table"; await afficherTable(tableActive); }
    else { modeVue = "dashboard"; await afficherTableauBord(tableActive); }
    rafraichirTables();
  } else {
    afficherVide();
  }
})();
// ── Configuration IA ──────────────────────────────────────────────────────
// Repliage générique des sections ".carte" (utilisé dans les configs de
// prompt IA — modal global et modal par table) : même mécanisme (▸/▾,
// classe "reduite") que les cartes du dashboard, mais délégué au document
// car ces cartes existent dans le HTML statique dès le chargement.
document.addEventListener("click", (ev) => {
  const entete = ev.target.closest(".carte > .entete-carte");
  if (!entete) return;
  const carte = entete.parentElement;
  const reduit = carte.classList.toggle("reduite");
  const btn = entete.querySelector(".btn-reduire");
  if (btn) btn.textContent = reduit ? "▸" : "▾";
});
async function ouvrirConfigIA() {
  document.getElementById("modal-config-ia-fond").style.display = "flex";
  try {
    const cfg = await api("/api/config-ia/");
    document.getElementById("cfg-moteur-host").value   = cfg.moteur_host || "";
    document.getElementById("cfg-moteur-port").value   = cfg.moteur_port || "";
    document.getElementById("cfg-moteur-url").value    = cfg.moteur_url_externe || "";
    document.getElementById("cfg-moteur-modele").value = cfg.moteur_modele || "";
    const _prov = Object.entries(_FOURNISSEURS_IA).find(([, v]) => v.url === cfg.moteur_url_externe);
    document.getElementById("cfg-moteur-fournisseur").value = _prov ? _prov[0] : "";
    document.getElementById("cfg-moteur-cle-statut").textContent =
      cfg.moteur_cle_api_definie ? tj("engine/static/engine/js/index_1.js:6596:37") : tj("engine/static/engine/js/index_1.js:6596:75");
    document.getElementById(cfg.moteur_type === "externe" ? "cfg-moteur-externe" : "cfg-moteur-local").checked = true;
    _cfgMoteurBasculer(cfg.moteur_type === "externe" ? "externe" : "local");
    document.getElementById("cfg-system-prompt").value  = cfg.system_prompt;
    document.getElementById("cfg-role-analysis").value  = cfg.role_analysis_script;
    document.getElementById("cfg-role-skill").value     = cfg.role_skill_script;
    document.getElementById("cfg-role-report").value    = cfg.role_report_summary;
    document.getElementById("cfg-role-other").value     = cfg.role_other;
    document.getElementById("cfg-role-document").value  = cfg.role_document || "";
    document.getElementById("cfg-regle-script").value   = cfg.regle_script;
    document.getElementById("cfg-regle-document").value = cfg.regle_document || "";
    document.getElementById("cfg-contraintes").value    = cfg.contraintes_syntaxe;
    document.getElementById("cfg-contraintes-document").value = cfg.contraintes_document || "";
    document.getElementById("cfg-modules-script").value   = cfg.modules_autorises_script || "";
    document.getElementById("cfg-modules-document").value = cfg.modules_autorises_document || "";
    _setCfgScript(cfg.script_exemple || "");
    _setCfgScriptDocument(cfg.script_exemple_document || "");
    // ── Canal formule ──
    document.getElementById("cfg-role-formule").value        = cfg.role_formule || "";
    document.getElementById("cfg-regle-formule").value       = cfg.regle_formule || "";
    document.getElementById("cfg-contraintes-formule").value = cfg.contraintes_formule || "";
    document.getElementById("cfg-modules-formule").value     = cfg.modules_autorises_formule || "";
    _setCfgScriptFormuleColonne(cfg.script_exemple_formule_colonne || "");
    _setCfgScriptFormuleTable(cfg.script_exemple_formule_table || "");
    // ── Canal graphique BI ──
    document.getElementById("cfg-role-graphique-bi").value        = cfg.role_graphique_bi || "";
    document.getElementById("cfg-regle-graphique-bi").value       = cfg.regle_graphique_bi || "";
    document.getElementById("cfg-contraintes-graphique-bi").value = cfg.contraintes_graphique_bi || "";
    document.getElementById("cfg-modules-graphique-bi").value     = cfg.modules_autorises_graphique_bi || "";
    _setCfgScriptGraphiqueBiColonne(cfg.script_exemple_graphique_bi_colonne || "");
    _setCfgScriptGraphiqueBiMultiples(cfg.script_exemple_graphique_bi_multiples || "");

    document.getElementById("cfg-role-couche-ontologie").value        = cfg.role_couche_ontologie || "";
    document.getElementById("cfg-regle-couche-ontologie").value       = cfg.regle_couche_ontologie || "";
    document.getElementById("cfg-contraintes-couche-ontologie").value = cfg.contraintes_couche_ontologie || "";
    document.getElementById("cfg-modules-couche-ontologie").value     = cfg.modules_autorises_couche_ontologie || "";
    _setCfgScriptCoucheOntologie(cfg.script_exemple_couche_ontologie || "");
    document.getElementById("cfg-role-modele-ia").value        = cfg.role_modele_ia || "";
    document.getElementById("cfg-regle-modele-ia").value       = cfg.regle_modele_ia || "";
    document.getElementById("cfg-contraintes-modele-ia").value = cfg.contraintes_modele_ia || "";
    document.getElementById("cfg-modules-modele-ia").value     = cfg.modules_autorises_modele_ia || "";
    _setCfgScriptModeleIa(cfg.script_exemple_modele_ia || "");
    document.getElementById("cfg-role-tache").value        = cfg.role_tache || "";
    document.getElementById("cfg-regle-tache").value       = cfg.regle_tache || "";
    document.getElementById("cfg-contraintes-tache").value = cfg.contraintes_tache || "";
    document.getElementById("cfg-modules-tache").value     = cfg.modules_autorises_tache || "";
    _setCfgScriptTache(cfg.script_exemple_tache || "");
  } catch(e) {
    console.error("ouvrirConfigIA:", e);
    notifierErreur(e && e.message ? e.message : tj("engine/static/engine/js/index_1.js:6645:50"));
  }
}
function fermerConfigIA() {
  document.getElementById("modal-config-ia-fond").style.display = "none";
}
const _FOURNISSEURS_IA = {
  openai:    {url: "https://api.openai.com/v1",                              modele: "gpt-4o-mini"},
  anthropic: {url: "https://api.anthropic.com/v1",                           modele: "claude-sonnet-4-6"},
  deepseek:  {url: "https://api.deepseek.com",                               modele: "deepseek-v4-pro"},
  qwen:      {url: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", modele: "qwen3.8-max"},
  glm:       {url: "https://api.z.ai/api/paas/v4",                           modele: "glm-5.3"},
};
document.getElementById("cfg-moteur-fournisseur").onchange = (ev) => {
  const p = _FOURNISSEURS_IA[ev.target.value];
  if (!p) return;
  document.getElementById("cfg-moteur-url").value    = p.url;
  document.getElementById("cfg-moteur-modele").value = p.modele;
};
function _cfgMoteurBasculer(type) {
  document.getElementById("cfg-moteur-panel-local").style.display   = type === "externe" ? "none" : "flex";
  document.getElementById("cfg-moteur-panel-externe").style.display = type === "externe" ? "flex" : "none";
}
document.getElementById("cfg-moteur-local").onchange   = () => _cfgMoteurBasculer("local");
document.getElementById("cfg-moteur-externe").onchange = () => _cfgMoteurBasculer("externe");
document.getElementById("btn-config-ia").onclick = ouvrirConfigIA;
document.getElementById("btn-fermer-config-ia").onclick = fermerConfigIA;
document.getElementById("btn-annuler-config-ia").onclick = fermerConfigIA;

document.getElementById("btn-sauvegarder-config-ia").onclick = async () => {
  const payload = {
    moteur_type:        document.getElementById("cfg-moteur-externe").checked ? "externe" : "local",
    moteur_host:        document.getElementById("cfg-moteur-host").value.trim(),
    moteur_port:        document.getElementById("cfg-moteur-port").value ? parseInt(document.getElementById("cfg-moteur-port").value, 10) : null,
    moteur_url_externe: document.getElementById("cfg-moteur-url").value.trim(),
    moteur_modele:      document.getElementById("cfg-moteur-modele").value.trim(),
    moteur_cle_api:     document.getElementById("cfg-moteur-cle").value,
    system_prompt:            document.getElementById("cfg-system-prompt").value,
    role_analysis_script:     document.getElementById("cfg-role-analysis").value,
    role_skill_script:        document.getElementById("cfg-role-skill").value,
    role_report_summary:      document.getElementById("cfg-role-report").value,
    role_other:               document.getElementById("cfg-role-other").value,
    role_document:            document.getElementById("cfg-role-document").value,
    regle_script:             document.getElementById("cfg-regle-script").value,
    regle_document:           document.getElementById("cfg-regle-document").value,
    contraintes_syntaxe:      document.getElementById("cfg-contraintes").value,
    contraintes_document:     document.getElementById("cfg-contraintes-document").value,
    modules_autorises_script:   document.getElementById("cfg-modules-script").value,
    modules_autorises_document: document.getElementById("cfg-modules-document").value,
    script_exemple:           _getCfgScript(),
    script_exemple_document:  _getCfgScriptDocument(),
    // ── Canal formule ──
    role_formule:             document.getElementById("cfg-role-formule").value,
    regle_formule:            document.getElementById("cfg-regle-formule").value,
    contraintes_formule:      document.getElementById("cfg-contraintes-formule").value,
    modules_autorises_formule: document.getElementById("cfg-modules-formule").value,
    script_exemple_formule_colonne: _getCfgScriptFormuleColonne(),
    script_exemple_formule_table:   _getCfgScriptFormuleTable(),
    // ── Canal graphique BI ──
    role_graphique_bi:             document.getElementById("cfg-role-graphique-bi").value,
    regle_graphique_bi:            document.getElementById("cfg-regle-graphique-bi").value,
    contraintes_graphique_bi:      document.getElementById("cfg-contraintes-graphique-bi").value,
    modules_autorises_graphique_bi: document.getElementById("cfg-modules-graphique-bi").value,
    script_exemple_graphique_bi_colonne:   _getCfgScriptGraphiqueBiColonne(),
    script_exemple_graphique_bi_multiples: _getCfgScriptGraphiqueBiMultiples(),

    role_couche_ontologie:        document.getElementById("cfg-role-couche-ontologie").value,
    regle_couche_ontologie:       document.getElementById("cfg-regle-couche-ontologie").value,
    contraintes_couche_ontologie: document.getElementById("cfg-contraintes-couche-ontologie").value,
    modules_autorises_couche_ontologie: document.getElementById("cfg-modules-couche-ontologie").value,
    script_exemple_couche_ontologie:    _getCfgScriptCoucheOntologie(),
    role_modele_ia:        document.getElementById("cfg-role-modele-ia").value,
    regle_modele_ia:       document.getElementById("cfg-regle-modele-ia").value,
    contraintes_modele_ia: document.getElementById("cfg-contraintes-modele-ia").value,
    modules_autorises_modele_ia: document.getElementById("cfg-modules-modele-ia").value,
    script_exemple_modele_ia:    _getCfgScriptModeleIa(),
    role_tache:        document.getElementById("cfg-role-tache").value,
    regle_tache:       document.getElementById("cfg-regle-tache").value,
    contraintes_tache: document.getElementById("cfg-contraintes-tache").value,
    modules_autorises_tache: document.getElementById("cfg-modules-tache").value,
    script_exemple_tache:    _getCfgScriptTache(),
  };
  try {
    await api("/api/config-ia/sauvegarder/", {method:"POST", body:JSON.stringify(payload)});
    notifierSucces(tj("engine/static/engine/js/index_1.js:6729:21"));
    fermerConfigIA();
  } catch(e) { notifierErreur(tj("engine/static/engine/js/index_1.js:6731:32")); }
};
// ── Onglets modal Config IA ───────────────────────────────────────────────
function cfgActiverOnglet(canal){
  const panels = {
    dashboard: document.getElementById("cfg-panel-dashboard"),
    document:  document.getElementById("cfg-panel-document"),
    formule:   document.getElementById("cfg-panel-formule"),
    "graphique-bi": document.getElementById("cfg-panel-graphique-bi"),
    "couche-ontologie": document.getElementById("cfg-panel-couche-ontologie"),
    "modele-ia": document.getElementById("cfg-panel-modele-ia"),
    "tache": document.getElementById("cfg-panel-tache"),
  };
  const tabs = {
    dashboard: document.getElementById("cfg-tab-dashboard"),
    document:  document.getElementById("cfg-tab-document"),
    formule:   document.getElementById("cfg-tab-formule"),
    "graphique-bi": document.getElementById("cfg-tab-graphique-bi"),
    "couche-ontologie": document.getElementById("cfg-tab-couche-ontologie"),
    "modele-ia": document.getElementById("cfg-tab-modele-ia"),
    "tache": document.getElementById("cfg-tab-tache"),
  };
  for(const cle in panels){
    panels[cle].style.display = (cle === canal) ? "flex" : "none";
    tabs[cle].classList.toggle("actif", cle === canal);
  }
}
// Restaurer le script d'exemple par défaut dans le champ correspondant de
// la modale Config IA (sans toucher aux autres champs).
document.getElementById("btn-restaurer-exemple").onclick = () => {
  _setCfgScript(window.EXEMPLE_SCRIPT_PYTHON || "");
};
document.getElementById("btn-import-exemple").onclick = () => {
  _inputImportScript(code => _setCfgScript(code));
};
document.getElementById("btn-copier-exemple").onclick = async () => {
  const code = _getCfgScript();
  if (!code) return;
  const ok = await copierPressePapiers(code);
  notifier(ok ? tj("engine/static/engine/js/index_1.js:6770:18") : tj("engine/static/engine/js/index_1.js:6770:47"), ok ? "succes" : "erreur");
};
document.getElementById("btn-restaurer-exemple-document").onclick = () => {
  _setCfgScriptDocument(window.EXEMPLE_SCRIPT_DOCUMENT || "");
};
document.getElementById("btn-import-exemple-document").onclick = () => {
  _inputImportScript(code => _setCfgScriptDocument(code));
};
document.getElementById("btn-copier-exemple-document").onclick = async () => {
  const code = _getCfgScriptDocument();
  if (!code) return;
  const ok = await copierPressePapiers(code);
  notifier(ok ? tj("engine/static/engine/js/index_1.js:6782:18") : tj("engine/static/engine/js/index_1.js:6782:46"), ok ? "succes" : "erreur");
};
document.getElementById("btn-restaurer-exemple-formule-colonne").onclick = () => {
  _setCfgScriptFormuleColonne(window.EXEMPLE_FORMULE_COLONNE || "");
};
document.getElementById("btn-import-exemple-formule-colonne").onclick = () => {
  _inputImportScript(code => _setCfgScriptFormuleColonne(code));
};
document.getElementById("btn-copier-exemple-formule-colonne").onclick = async () => {
  const code = _getCfgScriptFormuleColonne();
  if (!code) return;
  const ok = await copierPressePapiers(code);
  notifier(ok ? tj("engine/static/engine/js/index_1.js:6794:18") : tj("engine/static/engine/js/index_1.js:6794:55"), ok ? "succes" : "erreur");
};
document.getElementById("btn-restaurer-exemple-formule-table").onclick = () => {
  _setCfgScriptFormuleTable(window.EXEMPLE_FORMULE_TABLE || "");
};
document.getElementById("btn-import-exemple-formule-table").onclick = () => {
  _inputImportScript(code => _setCfgScriptFormuleTable(code));
};
document.getElementById("btn-copier-exemple-formule-table").onclick = async () => {
  const code = _getCfgScriptFormuleTable();
  if (!code) return;
  const ok = await copierPressePapiers(code);
  notifier(ok ? tj("engine/static/engine/js/index_1.js:6806:18") : tj("engine/static/engine/js/index_1.js:6806:53"), ok ? "succes" : "erreur");
};
document.getElementById("btn-restaurer-exemple-graphique-bi-colonne").onclick = () => {
  _setCfgScriptGraphiqueBiColonne(window.EXEMPLE_GRAPHIQUE_BI_COLONNE || "");
};
document.getElementById("btn-import-exemple-graphique-bi-colonne").onclick = () => {
  _inputImportScript(code => _setCfgScriptGraphiqueBiColonne(code));
};
document.getElementById("btn-copier-exemple-graphique-bi-colonne").onclick = async () => {
  const code = _getCfgScriptGraphiqueBiColonne();
  if (!code) return;
  const ok = await copierPressePapiers(code);
  notifier(ok ? tj("engine/static/engine/js/index_1.js:6818:18") : tj("engine/static/engine/js/index_1.js:6818:60"), ok ? "succes" : "erreur");
};
document.getElementById("btn-restaurer-exemple-graphique-bi-multiples").onclick = () => {
  _setCfgScriptGraphiqueBiMultiples(window.EXEMPLE_GRAPHIQUE_BI_MULTIPLES || "");
};
document.getElementById("btn-import-exemple-graphique-bi-multiples").onclick = () => {
  _inputImportScript(code => _setCfgScriptGraphiqueBiMultiples(code));
};
document.getElementById("btn-copier-exemple-graphique-bi-multiples").onclick = async () => {
  const code = _getCfgScriptGraphiqueBiMultiples();
  if (!code) return;
  const ok = await copierPressePapiers(code);
  notifier(ok ? tj("engine/static/engine/js/index_1.js:6830:18") : tj("engine/static/engine/js/index_1.js:6830:71"), ok ? "succes" : "erreur");
};
document.getElementById("btn-restaurer-exemple-couche-ontologie").onclick = () => {
  _setCfgScriptCoucheOntologie(window.EXEMPLE_COUCHE_ONTOLOGIE || "");
};
document.getElementById("btn-import-exemple-couche-ontologie").onclick = () => {
  _inputImportScript(code => _setCfgScriptCoucheOntologie(code));
};
document.getElementById("btn-copier-exemple-couche-ontologie").onclick = async () => {
  const code = _getCfgScriptCoucheOntologie();
  if (!code) return;
  const ok = await copierPressePapiers(code);
  notifier(ok ? tj("engine/static/engine/js/index_1.js:6842:18") : tj("engine/static/engine/js/index_1.js:6842:56"), ok ? "succes" : "erreur");
};
document.getElementById("btn-restaurer-exemple-modele-ia").onclick = () => {
  _setCfgScriptModeleIa(window.EXEMPLE_MODELE_IA || "");
};
document.getElementById("btn-import-exemple-modele-ia").onclick = () => {
  _inputImportScript(code => _setCfgScriptModeleIa(code));
};
document.getElementById("btn-copier-exemple-modele-ia").onclick = async () => {
  const code = _getCfgScriptModeleIa();
  if (!code) return;
  const ok = await copierPressePapiers(code);
  notifier(ok ? (window.MSG_MODELE_IA_COPIE || "") : tj("engine/static/engine/js/index_1.js:6854:55"), ok ? "succes" : "erreur");
};
document.getElementById("btn-restaurer-exemple-tache").onclick = () => {
  _setCfgScriptTache(window.EXEMPLE_TACHE || "");
};
document.getElementById("btn-import-exemple-tache").onclick = () => {
  _inputImportScript(code => _setCfgScriptTache(code));
};
document.getElementById("btn-copier-exemple-tache").onclick = async () => {
  const code = _getCfgScriptTache();
  if (!code) return;
  const ok = await copierPressePapiers(code);
  notifier(ok ? (window.MSG_TACHE_COPIE || "") : tj("engine/static/engine/js/index_1.js:6866:51"), ok ? "succes" : "erreur");
};
// Charger une configuration exemple complète (system prompt, rôles, règles,
// contraintes, script d'exemple) — utile pour voir la structure attendue
// avant de personnaliser.
document.getElementById("btn-charger-exemple-config").onclick = async () => {
  if(!await GY.confirmer(tj("engine/static/engine/js/index_1.js:6872:16"))) return;

  document.getElementById("cfg-system-prompt").value =
    tj("engine/static/engine/js/index_1.js:6875:6") +
    tj("engine/static/engine/js/index_1.js:6876:6");

  document.getElementById("cfg-role-analysis").value =
    tj("engine/static/engine/js/index_1.js:6879:6") +
    tj("engine/static/engine/js/index_1.js:6880:6") +
    tj("engine/static/engine/js/index_1.js:6881:6");

  document.getElementById("cfg-role-skill").value =
    tj("engine/static/engine/js/index_1.js:6884:6") +
    tj("engine/static/engine/js/index_1.js:6885:6") +
    tj("engine/static/engine/js/index_1.js:6886:6") +
    tj("engine/static/engine/js/index_1.js:6887:6");

  document.getElementById("cfg-role-report").value =
    tj("engine/static/engine/js/index_1.js:6890:6") +
    tj("engine/static/engine/js/index_1.js:6891:6");

  document.getElementById("cfg-role-other").value =
    tj("engine/static/engine/js/index_1.js:6894:6");

  document.getElementById("cfg-regle-script").value =
    tj("engine/static/engine/js/index_1.js:6897:6") +
    tj("engine/static/engine/js/index_1.js:6898:6") +
    tj("engine/static/engine/js/index_1.js:6899:6") +
    "{\n" +
    tj("engine/static/engine/js/index_1.js:6901:6") +
    "  \"pourquoi\": str,\n" +
    "  \"comment\": str,\n" +
    tj("engine/static/engine/js/index_1.js:6904:6") +
    "  \"figures\": list,\n" +
    "  \"explication\": str,\n" +
    tj("engine/static/engine/js/index_1.js:6907:6") +
    "  \"kpi\": dict,\n" +
    "  \"conclusion\": str,\n" +
    "  \"recommandations\": list\n" +
    "}\n" +
    tj("engine/static/engine/js/index_1.js:6912:6") +
    tj("engine/static/engine/js/index_1.js:6913:6");

  document.getElementById("cfg-contraintes").value =
    tj("engine/static/engine/js/index_1.js:6916:6") +
    tj("engine/static/engine/js/index_1.js:6917:6") +
    tj("engine/static/engine/js/index_1.js:6918:6") +
    "- Utiliser uniquement : pandas, numpy, scipy, matplotlib, statsmodels, scikit-learn, math, json, datetime, re, collections.\n" +
    tj("engine/static/engine/js/index_1.js:6920:6") +
    tj("engine/static/engine/js/index_1.js:6921:6") +
    tj("engine/static/engine/js/index_1.js:6922:6") +
    tj("engine/static/engine/js/index_1.js:6923:6");

  document.getElementById("cfg-role-document").value =
    tj("engine/static/engine/js/index_1.js:6926:6") +
    tj("engine/static/engine/js/index_1.js:6927:6") +
    tj("engine/static/engine/js/index_1.js:6928:6");

  document.getElementById("cfg-regle-document").value =
    tj("engine/static/engine/js/index_1.js:6931:6") +
    tj("engine/static/engine/js/index_1.js:6932:6") +
    tj("engine/static/engine/js/index_1.js:6933:6") +
    "  type_document, titre, reference, date, emetteur (dict), destinataire (dict),\n" +
    "  lignes (list of dict), totaux (dict), notes, pied_page.\n" +
    tj("engine/static/engine/js/index_1.js:6936:6") +
    tj("engine/static/engine/js/index_1.js:6937:6") +
    tj("engine/static/engine/js/index_1.js:6938:6") +
    tj("engine/static/engine/js/index_1.js:6939:6") +
    tj("engine/static/engine/js/index_1.js:6940:6");

  document.getElementById("cfg-contraintes-document").value =
    "CONTRAINTES PYTHON ABSOLUES :\n" +
    "1. True / False / None. JAMAIS true / false / null.\n" +
    "2. JAMAIS input(), open(), requests, exec(), eval(), pandas, numpy.\n" +
    tj("engine/static/engine/js/index_1.js:6946:6") +
    tj("engine/static/engine/js/index_1.js:6947:6") +
    tj("engine/static/engine/js/index_1.js:6948:6") +
    tj("engine/static/engine/js/index_1.js:6949:6") +
    tj("engine/static/engine/js/index_1.js:6950:6");

  document.getElementById("cfg-modules-script").value =
    "pandas, numpy, scipy, statsmodels, sklearn, matplotlib, matplotlib.pyplot, networkx, math, statistics, json";
  document.getElementById("cfg-modules-document").value = "datetime, math, json, decimal";

  document.getElementById("cfg-role-formule").value =
    tj("engine/static/engine/js/index_1.js:6957:6") +
    tj("engine/static/engine/js/index_1.js:6958:6") +
    tj("engine/static/engine/js/index_1.js:6959:6") +
    tj("engine/static/engine/js/index_1.js:6960:6");

  document.getElementById("cfg-regle-formule").value =
    tj("engine/static/engine/js/index_1.js:6963:6") +
    tj("engine/static/engine/js/index_1.js:6964:6") +
    tj("engine/static/engine/js/index_1.js:6965:6") +
    tj("engine/static/engine/js/index_1.js:6966:6") +
    tj("engine/static/engine/js/index_1.js:6967:6") +
    tj("engine/static/engine/js/index_1.js:6968:6");

  document.getElementById("cfg-contraintes-formule").value =
    "CONTRAINTES PYTHON ABSOLUES :\n" +
    "1. True / False / None. JAMAIS true / false / null.\n" +
    "2. JAMAIS input(), open(), requests, exec(), eval().\n" +
    tj("engine/static/engine/js/index_1.js:6974:6") +
    tj("engine/static/engine/js/index_1.js:6975:6") +
    tj("engine/static/engine/js/index_1.js:6976:6") +
    tj("engine/static/engine/js/index_1.js:6977:6");

  document.getElementById("cfg-modules-formule").value =
    "pandas, numpy, scipy, statsmodels, math, statistics, decimal, prophet";

  document.getElementById("cfg-role-graphique-bi").value =
    tj("engine/static/engine/js/index_1.js:6983:6") +
    tj("engine/static/engine/js/index_1.js:6984:6") +
    tj("engine/static/engine/js/index_1.js:6985:6") +
    "lisible. Tu codes uniquement.";

  document.getElementById("cfg-regle-graphique-bi").value =
    tj("engine/static/engine/js/index_1.js:6989:6") +
    tj("engine/static/engine/js/index_1.js:6990:6") +
    tj("engine/static/engine/js/index_1.js:6991:6") +
    tj("engine/static/engine/js/index_1.js:6992:6") +
    tj("engine/static/engine/js/index_1.js:6993:6") +
    tj("engine/static/engine/js/index_1.js:6994:6") +
    tj("engine/static/engine/js/index_1.js:6995:6");

  document.getElementById("cfg-contraintes-graphique-bi").value =
    "CONTRAINTES PYTHON ABSOLUES :\n" +
    "1. True / False / None. JAMAIS true / false / null.\n" +
    "2. JAMAIS input(), open(), requests, exec(), eval().\n" +
    tj("engine/static/engine/js/index_1.js:7001:6") +
    tj("engine/static/engine/js/index_1.js:7002:6") +
    tj("engine/static/engine/js/index_1.js:7003:6") +
    tj("engine/static/engine/js/index_1.js:7004:6") +
    tj("engine/static/engine/js/index_1.js:7005:6");

  document.getElementById("cfg-modules-graphique-bi").value =
    "pandas, numpy, matplotlib, matplotlib.pyplot, math, statistics";

  document.getElementById("cfg-role-couche-ontologie").value =
    tj("engine/static/engine/js/index_1.js:7011:6") +
    tj("engine/static/engine/js/index_1.js:7012:6") +
    tj("engine/static/engine/js/index_1.js:7013:6");

  document.getElementById("cfg-regle-couche-ontologie").value =
    tj("engine/static/engine/js/index_1.js:7016:6") +
    tj("engine/static/engine/js/index_1.js:7017:6") +
    tj("engine/static/engine/js/index_1.js:7018:6") +
    tj("engine/static/engine/js/index_1.js:7019:6") +
    tj("engine/static/engine/js/index_1.js:7020:6");

  document.getElementById("cfg-contraintes-couche-ontologie").value =
    "CONTRAINTES PYTHON ABSOLUES :\n" +
    "1. True / False / None. JAMAIS true / false / null.\n" +
    "2. JAMAIS input(), open(), requests, exec(), eval().\n" +
    tj("engine/static/engine/js/index_1.js:7026:6") +
    tj("engine/static/engine/js/index_1.js:7027:6") +
    tj("engine/static/engine/js/index_1.js:7028:6");

  document.getElementById("cfg-modules-couche-ontologie").value =
    "networkx, math, statistics, collections, itertools";

  _setCfgScript(window.EXEMPLE_SCRIPT_PYTHON || "");
  _setCfgScriptDocument(window.EXEMPLE_SCRIPT_DOCUMENT || "");
  _setCfgScriptFormuleColonne(window.EXEMPLE_FORMULE_COLONNE || "");
  _setCfgScriptFormuleTable(window.EXEMPLE_FORMULE_TABLE || "");
  _setCfgScriptGraphiqueBiColonne(window.EXEMPLE_GRAPHIQUE_BI_COLONNE || "");
  _setCfgScriptGraphiqueBiMultiples(window.EXEMPLE_GRAPHIQUE_BI_MULTIPLES || "");
  _setCfgScriptCoucheOntologie(window.EXEMPLE_COUCHE_ONTOLOGIE || "");
  document.getElementById("cfg-modules-modele-ia").value = "numpy, sklearn, math, statistics, json";
  _setCfgScriptModeleIa(window.EXEMPLE_MODELE_IA || "");
  document.getElementById("cfg-modules-tache").value = "pandas, numpy, math, statistics, json, datetime";
  _setCfgScriptTache(window.EXEMPLE_TACHE || "");

  notifierSucces(tj("engine/static/engine/js/index_1.js:7045:19"));
};
// Fermer en cliquant sur le fond
document.getElementById("modal-config-ia-fond").onclick = (e) => {
  if(e.target === document.getElementById("modal-config-ia-fond")) fermerConfigIA();
};
// ── Reprise automatique à la réouverture du navigateur ────────────────────
// Quand l'utilisateur revient après avoir fermé/minimisé, on recharge
// silencieusement le tableau de bord actif pour refléter d'éventuels
// changements. Aucun polling : chaque appel serveur est désormais
// synchrone (la réponse HTTP contient toujours le résultat final).
// Sur téléphone, choisir un fichier ouvre le sélecteur du système : la page passe en arrière-plan puis revient (visibilitychange).
// Ce retour-là ne doit PAS recharger la vue : le formulaire en cours de saisie serait effacé. On mémorise donc l'ouverture d'un
// sélecteur de fichier (clic ou choix sur un <input type=file>) et on ignore la reprise pendant les 2 minutes qui suivent.
let _derniereSelectionFichier = 0;
document.addEventListener("click", (e) => { if(e.target && e.target.matches && e.target.matches("input[type=file]")) _derniereSelectionFichier = Date.now(); }, true);
document.addEventListener("change", (e) => { if(e.target && e.target.matches && e.target.matches("input[type=file]")) _derniereSelectionFichier = Date.now(); }, true);
async function reprendreApresRetour() {
  if(!tableActive) return;
  if(Date.now() - _derniereSelectionFichier < 120000) return;
  if(Array.from(document.querySelectorAll("input[type=file]")).some(i => i.files && i.files.length)) return;   // un fichier est déjà choisi dans un formulaire ouvert
  try {
    // Recharger le dashboard silencieusement
    if(modeVue === "dashboard") {
      await afficherTableauBord(tableActive);
    } else if(modeVue === "table") {
      await afficherTable(tableActive);
    } else if(modeVue === "documents") {
      await afficherDocuments(tableActive);
    }
  } catch(e) {}
}
// visibilitychange : déclenché quand l'onglet redevient actif
document.addEventListener("visibilitychange", () => {
  if(document.visibilityState === "visible") {
    reprendreApresRetour();
  }
});
// pageshow : déclenché quand on revient via historique (back/forward cache)
window.addEventListener("pageshow", (e) => {
  if(e.persisted) reprendreApresRetour();
});


// ── Tables réservées à un client (bouton de la ligne dans la table des agents et clients) ─────────────────────────────
async function ouvrirTablesClientLigne(tableId, ligneId){
  const url = `/api/tables/${tableId}/lignes/${ligneId}/tables-client/`;
  let data;
  try { data = await api(url); } catch (e) { return; }
  const fond = document.createElement("div");
  fond.style.cssText = "position:fixed;inset:0;background:color-mix(in srgb,var(--voile,#000) 60%,transparent);display:flex;align-items:center;justify-content:center;z-index:3000;padding:16px";
  const cases = (data.tables || []).map(x => `
    <label style="display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer">
      <input type="checkbox" value="${x.id}"${(data.autorisees || []).includes(x.id) ? " checked" : ""}> ${escapeHtml(x.nom)}
    </label>`).join("") || `<span style="font-size:12px;color:var(--muted,#888)">${escapeHtml(tj("engine/static/engine/js/index_1.js:g:tables_client_aucune"))}</span>`;
  fond.innerHTML = `
    <div style="background:var(--bg-2,var(--panel,#1b1b1b));color:inherit;border:1px solid var(--border,#2a2a2a);border-radius:10px;padding:18px;max-width:420px;width:100%">
      <h3 style="margin:0 0 10px">${escapeHtml(tj("engine/static/engine/js/index_1.js:g:tables_client_titre"))}</h3>
      <div style="display:flex;flex-direction:column;gap:8px;max-height:260px;overflow-y:auto;margin-bottom:8px">${cases}</div>
      <p style="font-size:11px;color:var(--muted,#888);margin:0 0 12px">${escapeHtml(tj("engine/static/engine/js/index_1.js:g:tables_client_aide"))}</p>
      <div style="display:flex;justify-content:flex-end;gap:8px">
        <button type="button" data-act="annuler">${escapeHtml(tj("engine/static/engine/js/index_1.js:g:tables_client_annuler"))}</button>
        <button type="button" data-act="ok" class="principal">${escapeHtml(tj("engine/static/engine/js/index_1.js:g:tables_client_enregistrer"))}</button>
      </div>
    </div>`;
  document.body.appendChild(fond);
  const fermer = () => fond.remove();
  fond.addEventListener("click", ev => { if(ev.target === fond) fermer(); });
  fond.querySelector('[data-act="annuler"]').onclick = fermer;
  fond.querySelector('[data-act="ok"]').onclick = async () => {
    const tables = [...fond.querySelectorAll('input[type="checkbox"]:checked')].map(c => Number(c.value));
    try {
      await api(url, {method: "POST", body: JSON.stringify({tables})});
      notifierSucces(tj("engine/static/engine/js/index_1.js:g:tables_client_ok"));
      fermer();
    } catch (e) { /* erreur déjà notifiée par api() */ }
  };
}
