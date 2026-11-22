// ── Intro animation (identique a index.html) ─────────────────────────────────
(function(){
  const overlay = document.getElementById("intro-overlay");
  function dismissIntro(){
    overlay.classList.add("sortie");
    setTimeout(() => overlay.remove(), 700);
  }
  if(document.readyState === "complete"){
    setTimeout(dismissIntro, 1800);
  } else {
    window.addEventListener("load", () => setTimeout(dismissIntro, 1800));
  }
})();

const TABLES = JSON.parse(document.getElementById('tables-data').textContent);
let tableActive = null;
let fichiers = {};

// ── Sidebar ──────────────────────────────────────────────────────────────────
// Même comportement que les pages de l'entreprise (sidebar_toggle.js) : le bouton ☰ de l'en-tête est toujours visible.
// - PC (> 640 px) : masque / affiche la sidebar ; l'état est mémorisé (localStorage, jamais bloquant).
// - Mobile (<= 640 px, seuil du CSS de cette page) : tiroir + voile.
// - Raccourci Ctrl+B ; « resize » est déclenché après chaque bascule sur PC.
const SIDEBAR_CLE = 'gy_sidebar_masque_page-client';
const sidebarMobile = () => window.innerWidth <= 640;
function majBoutonSidebar() {
  const barre = document.getElementById('sidebar'), bouton = document.getElementById('btn-toggle-sidebar');
  if (!barre || !bouton) return;
  const visible = sidebarMobile() ? barre.classList.contains('ouvert') : !barre.classList.contains('masque');
  bouton.setAttribute('aria-expanded', visible ? 'true' : 'false');
}
function toggleSidebar() {
  const barre = document.getElementById('sidebar'), voile = document.getElementById('sidebar-overlay');
  if (sidebarMobile()) {
    const ouvert = barre.classList.toggle('ouvert');
    voile.classList.toggle('ouvert', ouvert);
  } else {
    const masque = barre.classList.toggle('masque');
    try { localStorage.setItem(SIDEBAR_CLE, masque ? '1' : '0'); } catch (e) {}
    window.dispatchEvent(new Event('resize'));
  }
  majBoutonSidebar();
}
function fermerSidebar() {
  document.getElementById('sidebar').classList.remove('ouvert');
  document.getElementById('sidebar-overlay').classList.remove('ouvert');
  majBoutonSidebar();
}
(function initSidebar() {
  const barre = document.getElementById('sidebar');
  if (!barre) return;
  try { if (localStorage.getItem(SIDEBAR_CLE) === '1' && !sidebarMobile()) barre.classList.add('masque'); } catch (e) {}
  majBoutonSidebar();
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'b') {
      e.preventDefault();
      toggleSidebar();
    }
  });
  window.addEventListener('resize', () => { if (!sidebarMobile()) fermerSidebar(); else majBoutonSidebar(); });
})();

// ── Navigation ───────────────────────────────────────────────────────────────
function ouvrirTable(id) {
  tableActive = TABLES.find(t => t.id === id);
  if (!tableActive) return;
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('actif', parseInt(el.dataset.id) === id);
  });
  document.getElementById('ecran-accueil').style.display = 'none';
  document.getElementById('confirmation').classList.remove('visible');
  document.getElementById('zone-formulaire').classList.add('visible');
  document.getElementById('form-titre').textContent = tableActive.nom;
  document.getElementById('form-desc').textContent = tableActive.description;

  // Deux bascules totalement independantes, cote entreprise :
  //  - expose_client            -> formulaire de saisie visible ("Client")
  //  - masquer_formulaire_client -> recherche visible ("Recherche seule")
  // Une table qui apparait dans le menu a forcement au moins une des deux
  // actives (sinon elle n'est meme pas listee, voir accessible_client
  // cote backend) — mais l'une des deux peut tres bien etre inactive,
  // auquel cas seul l'autre bloc s'affiche.
  const afficherFormulaire = !!tableActive.expose_client;
  const afficherRecherche = !!tableActive.masquer_formulaire_client;
  document.getElementById('le-form').style.display = afficherFormulaire ? '' : 'none';
  document.getElementById('form-actions').style.display = afficherFormulaire ? '' : 'none';
  // Le separateur n'a de sens que si les deux blocs sont visibles.
  document.querySelector('#zone-formulaire .form-sep').style.display =
    (afficherFormulaire && afficherRecherche) ? '' : 'none';

  if (afficherFormulaire) {
    construireFormulaire(tableActive.champs);
    fichiers = {};
  }
  // Rendu « vivant » du formulaire : image de garde, couleurs, polices… choisis par l'agent dans l'éditeur visuel (formulaire_theme.js).
  if (window.gyFormTheme) gyFormTheme.appliquer(document.getElementById('zone-formulaire'), tableActive.theme_formulaire || {});
  // La recherche par QR code n'a de sens que sur la table spéciale
  // agents et clients (matricule/empreinte carte) — masquée pour toute
  // autre table, même quand la recherche classique est visible.
  document.getElementById('btn-scanner-qr-client').style.display =
    tableActive.est_table_membres ? '' : 'none';

  if (afficherRecherche) {
    chargerRecherche(id);
  } else {
    document.getElementById('zone-recherche').classList.remove('visible');
  }
  fermerSidebar();
}

// ── Recherche dans la table du formulaire (donnees deja enregistrees) ────────
// Meme service que celui utilise cote entreprise (GET /api/tables/<id>/lignes/),
// deja limite par le backend aux tables accessible_client=True pour un
// client (role lecteur). N'est appelee que si la bascule "Recherche seule"
// (masquer_formulaire_client) est active pour cette table — voir
// ouvrirTable() — independamment de la bascule formulaire.
let lignesTableActive = [];
let champsTableActive = [];

async function chargerRecherche(id) {
  const zone = document.getElementById('zone-recherche');
  const input = document.getElementById('recherche-input');
  input.value = '';
  lignesTableActive = [];
  champsTableActive = [];
  zone.classList.remove('visible');
  try {
    const res = await fetch(`/api/tables/${id}/lignes/`, { headers: { 'Accept': 'application/json' } });
    if (res.status === 402) { _afficher402(await res.json()); return; }
    if (!res.ok) return; // pas exposee / pas accessible -> pas de bloc recherche
    const data = await res.json();
    champsTableActive = (data.table && data.table.champs) || [];
    lignesTableActive = [];  // le serveur ne renvoie aucune ligne sans mot-clé (recherche uniquement)
    if (!champsTableActive.length) return;
    zone.classList.add('visible');
    // Recherche pure : rien n'est affiché tant qu'aucun mot n'est tapé —
    // le client ne doit pas pouvoir parcourir tout le tableau sans chercher
    // un élément précis (voir filtrerRecherche, même état vide).
    document.getElementById('recherche-resultats').innerHTML = tj("engine/static/engine/js/client_1.js:103:65");
    document.getElementById('recherche-compte').textContent = '';
  } catch (e) {
    zone.classList.remove('visible');
  }
}

document.getElementById('btn-exporter-recherche-pdf').onclick = () => {
  if (!tableActive) return;
  const q = document.getElementById('recherche-input').value.trim();
  if (q.length < 2) { toast(tj("engine/static/engine/js/client_1.js:113:30"), 'err'); return; }
  gyTelecharger(`/api/tables/${tableActive.id}/exporter-pdf/?q=${encodeURIComponent(q)}`, `${tableActive.slug}.pdf`,
    { bouton: document.getElementById('btn-exporter-recherche-pdf'), succes: () => toast(tj("engine/static/engine/js/client_1.js:115:91"), 'ok') });
};

// Recherche CÔTÉ SERVEUR (GET /api/tables/<id>/lignes/?q=…) : le client ne reçoit que les lignes qui
// correspondent à son mot-clé (50 au plus), jamais la table entière. Anti-rebond de 350 ms et annulation
// de la requête précédente pour ne pas débiter une recherche par touche frappée.
let _minuterieRecherche = null;
let _rechercheEnCours = null;
function filtrerRecherche() {
  const q = document.getElementById('recherche-input').value.trim();
  clearTimeout(_minuterieRecherche);
  if (q.length < 2) {
    lignesTableActive = [];
    document.getElementById('recherche-resultats').innerHTML = tj("engine/static/engine/js/client_1.js:128:65");
    document.getElementById('recherche-compte').textContent = '';
    return;
  }
  _minuterieRecherche = setTimeout(() => _lancerRecherche(q), 350);
}

async function _lancerRecherche(q) {
  if (!tableActive) return;
  if (_rechercheEnCours) _rechercheEnCours.abort();
  const ctrl = new AbortController();
  _rechercheEnCours = ctrl;
  document.getElementById('recherche-compte').textContent = tj("engine/static/engine/js/client_1.js:140:62");
  try {
    const res = await fetch(`/api/tables/${tableActive.id}/lignes/?q=${encodeURIComponent(q)}`,
      { headers: { 'Accept': 'application/json' }, signal: ctrl.signal });
    if (res.status === 402) { _afficher402(await res.json()); return; }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { toast(data.erreur || tj("engine/static/engine/js/client_1.js:146:42"), 'err'); return; }
    lignesTableActive = data.lignes || [];
    rendreRecherche(lignesTableActive);
    const total = (data.pagination && data.pagination.total) || 0;
    if (total > lignesTableActive.length) {
      document.getElementById('recherche-compte').textContent =
        tj("engine/static/engine/js/client_1.js:152:10", {v1: lignesTableActive.length, v2: total});
    }
  } catch (e) {
    if (e.name !== 'AbortError') toast(tj("engine/static/engine/js/client_1.js:155:41"), 'err');
  } finally {
    if (_rechercheEnCours === ctrl) _rechercheEnCours = null;
  }
}

// Recherche securisee par QR code : capture image caméra (mécanisme deja
// utilisé pour les champs image/son/vidéo), envoie au backend qui déchiffre
// et retrouve la ligne en base, puis l'affiche via le même rendu que la
// recherche classique (rendreRecherche) — jamais le contenu du QR affiché
// directement.
document.getElementById('btn-scanner-qr-client').addEventListener('click', async () => {
  if (!tableActive) return;
  let fichier;
  try { fichier = await ouvrirCaptureMedia('image'); } catch(e) { return; }
  if (!fichier) return;
  const corps = new FormData(); corps.append('image', fichier);
  try {
    const res = await fetch(`/api/tables/${tableActive.id}/qrcode-rechercher/`, { method:'POST', headers:{'X-CSRFToken':getCsrf()}, body: corps });
    const data = await res.json();
    if (!res.ok) { GY.alerter(data.erreur || tj("engine/static/engine/js/client_1.js:209:47")); return; }
    document.getElementById('recherche-input').value = '';
    rendreRecherche([data.ligne]);
  } catch(e) { GY.alerter(tj("engine/static/engine/js/client_1.js:178:23")); }
});

function rendreRecherche(lignes) {
  const zone = document.getElementById('recherche-resultats');
  const compte = document.getElementById('recherche-compte');
  compte.textContent = lignes.length + (lignes.length > 1 ? tj("engine/static/engine/js/client_1.js:218:62") : tj("engine/static/engine/js/client_1.js:218:77"));
  if (!lignes.length) {
    zone.innerHTML = tj("engine/static/engine/js/client_1.js:186:23");
    return;
  }
  // Limite d'affichage raisonnable (colonnes et lignes) pour rester lisible
  // sur un tableau simple sans pagination ni tri.
  const colonnes = champsTableActive;   // tous les champs, comme la grille côté agent (défilement horizontal si besoin)
  const LIMITE_LIGNES = 200;
  // Pas de bouton PDF par ligne ici : l'export unitaire n'a d'intérêt que
  // côté admin (page index) ; côté client, seul l'export de la liste
  // complète (bouton dans l'entête, voir btn-exporter-recherche-pdf) et le
  // reçu PDF de sa propre soumission (bouton #btn-pdf) sont nécessaires.
  // Rendu « grille de tarifs » : une colonne de couleur par résultat (onglet arrondi + pastille numérotée),
  // une ligne par champ, lignes alternées. Jusqu'à 4 résultats on compare en colonnes (champs en lignes) ;
  // au-delà on garde un résultat par ligne, mais avec les mêmes onglets colorés (lisible pour 50 résultats).
  const visibles = lignes.slice(0, LIMITE_LIGNES);
  const comparatif = false;   // même présentation que côté agent : une ligne par résultat, une colonne par champ (plus de grille de tarifs transposée)
  const cls = (i) => 'pt-c' + (i % 3);
  let html = '<div class="pt-wrap"><table class="tableau-recherche ' + (comparatif ? 'pt-comparatif' : 'pt-liste') + '">';
  if (comparatif) {
    html += '<thead><tr><th class="pt-coin"></th>';
    visibles.forEach((ligne, i) => {
      const titre = _valeurRecherche(ligne[colonnes[0].slug]) || ('#' + (i + 1));
      html += `<th class="pt-onglet ${cls(i)}"><span class="pt-pastille">${i + 1}</span><span class="pt-titre">${_echapper(titre)}</span></th>`;
    });
    html += '</tr></thead><tbody>';
    colonnes.slice(1).forEach(c => {
      html += `<tr><th scope="row" class="pt-etiquette">${_echapper(c.nom)}</th>`;
      visibles.forEach(ligne => { html += `<td>${_renduValeurMedia(c, ligne[c.slug])}</td>`; });
      html += '</tr>';
    });
    html += '</tbody>';
  } else {
    html += '<thead><tr>';
    colonnes.forEach(c => { html += `<th>${_echapper(c.nom)}</th>`; });
    html += '</tr></thead><tbody>';
    visibles.forEach(ligne => {
      html += '<tr>';
      colonnes.forEach(c => { html += `<td>${_renduValeurMedia(c, ligne[c.slug])}</td>`; });
      html += '</tr>';
    });
    html += '</tbody>';
  }
  html += '</table></div>';
  if (zone.parentElement) zone.parentElement.classList.toggle('pt-comparatif-scroll', comparatif);
  if (lignes.length > LIMITE_LIGNES) {
    html += `<div class="recherche-vide">${tj("engine/static/engine/js/client_1.js:207:43")} ${lignes.length} ${tj("engine/static/engine/js/client_1.js:207:83")} ${LIMITE_LIGNES} ${tj("engine/static/engine/js/client_1.js:207:111")}</div>`;
  }
  zone.innerHTML = html;
}

function _valeurRecherche(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

// Rendu HTML d'une valeur de champ pour affichage (recherche, confirmation) —
// les fichiers image/son/vidéo sont rendus comme média plutôt qu'affichés
// en URL brute (même logique que valeurCellule côté admin dans index.html).
function _renduValeurMedia(champ, val) {
  if (val === null || val === undefined || val === '') return '';
  const btnOuvrir = (type) => `<button type="button" class="btn-ouvrir-media" data-type="${type}" data-url="${_echapper(val)}" data-titre="${_echapper(champ.nom||tj("engine/static/engine/js/client_1.js:223:164"))}" title="${tj("engine/static/engine/js/client_1.js:a:223:222")}" style="color:var(--accent);background:transparent;text-decoration:none;font-size:11px;border:1px solid var(--border);border-radius:4px;padding:2px 6px;line-height:1.6;cursor:pointer;display:inline-flex;align-items:center;gap:3px;flex-shrink:0">${tj("engine/static/engine/js/client_1.js:223:456")}</button>`;
  if (champ.type === 'image') {
    return `<span style="display:inline-flex;align-items:center;gap:6px">
      <img src="${val}" style="max-height:60px;max-width:120px;border-radius:4px">
      ${btnOuvrir('image')}
    </span>`;
  }
  if (champ.type === 'son') {
    return `<span style="display:inline-flex;align-items:center;gap:6px">
      <audio controls src="${val}" style="height:32px;max-width:220px"></audio>
      ${btnOuvrir('son')}
    </span>`;
  }
  if (champ.type === 'video') {
    return `<span style="display:inline-flex;align-items:center;gap:6px">
      <video controls src="${val}" style="max-height:80px;max-width:160px;border-radius:4px"></video>
      ${btnOuvrir('video')}
    </span>`;
  }
  if (champ.type === 'fichier') {
    // PDF / fichier texte : bouton d'aperçu (même visionneuse que les images et vidéos), le lien d'ouverture reste.
    return `<span style="display:inline-flex;align-items:center;gap:6px;flex-wrap:wrap"><a href="${val}" target="_blank" rel="noopener" class="btn petit">${tj("engine/static/engine/js/client_1.js:243:90")}</a>${ApercuFichier.bouton(val, champ.nom || tj("engine/static/engine/js/client_1.js:223:164"))}</span>`;
  }
  return _echapper(_valeurRecherche(val));
}

// ── Visionneuse : espace dédié pour ouvrir en grand une image/son/vidéo
// depuis un champ média (bouton "⤢ Ouvrir" injecté par _renduValeurMedia),
// même logique que côté admin dans index.html.
function ouvrirVisionneuse(type, url, titre){
  document.getElementById("visionneuse-titre").textContent = titre || tj("engine/static/engine/js/client_1.js:252:72");
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

function _echapper(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ── Construction formulaire ──────────────────────────────────────────────────
// Bloc d'un champ : libellé (+ couleur choisie côté agent), widget de saisie et zone de message d'erreur.
function creerGroupeChamp(champ) {
  const div = document.createElement('div');
  div.className = 'groupe';
  div.dataset.slug = champ.slug;
  const label = document.createElement('label');
  label.innerHTML = champ.nom + (champ.obligatoire ? ' <span class="req">*</span>' : '');
  if (/^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(champ.couleur || '')) {   // couleur du champ choisie côté agent : le libellé la reprend (texte noir ou blanc selon le fond)
    const v = [1, 3, 5].map(i => parseInt(champ.couleur.slice(i, i + 2), 16));
    label.style.cssText = 'background:' + champ.couleur + ';color:' + (champ.couleur.length === 9 && parseInt(champ.couleur.slice(7, 9), 16) < 153 ? 'inherit' : ((0.299 * v[0] + 0.587 * v[1] + 0.114 * v[2]) > 150 ? gyCouleur("--fond-sombre","#111111") : gyCouleur("--texte-sombre","#ffffff"))) + ';padding:2px 8px;border-radius:4px;display:inline-block';
  }
  div.appendChild(label);
  div.appendChild(creerWidget(champ));
  const errDiv = document.createElement('div');
  errDiv.className = 'champ-erreur-msg';
  errDiv.id = 'err-' + champ.slug;
  div.appendChild(errDiv);
  return div;
}

function construireFormulaire(champs) {
  const form = document.getElementById('le-form');
  // Formulaire conditionnel : champs répartis en sections avec renvois selon les réponses (formulaire_conditionnel_client.js).
  if (window.gyFormCond && gyFormCond.actif(tableActive) && tableActive.champs === champs) { gyFormCond.construire(tableActive, form, creerGroupeChamp); return; }
  const actions = document.getElementById('form-actions'); if (actions) actions.style.display = '';
  form.innerHTML = '';
  champs.forEach(champ => {
    if ((champ.type === 'horodatage' || champ.type === 'id_client')) return;   // ecrit par le serveur, jamais saisi
    form.appendChild(creerGroupeChamp(champ));
  });
}

// ── Capture média directe (caméra/micro) pour les champs image/son/vidéo ────
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
    titre.textContent = kind === "image" ? tj("engine/static/engine/js/client_1.js:362:45") : kind === "son" ? tj("engine/static/engine/js/client_1.js:362:86") : tj("engine/static/engine/js/client_1.js:362:112");
    video.style.display = "none"; photoApercu.style.display = "none";
    audioApercu.style.display = "none"; videoApercu.style.display = "none";
    audioApercu.removeAttribute("src"); videoApercu.removeAttribute("src");
    btnUtiliser.style.display = "none"; btnReprendre.style.display = "none"; btnBasculer.style.display = "none";
    btnAction.style.display = "inline-block";
    btnAction.textContent = kind === "image" ? "📸 Capturer" : tj("engine/static/engine/js/client_1.js:368:64");
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
      erreur.textContent = tj("engine/static/engine/js/client_1.js:384:29") + (err && err.message ? err.message : err);
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

    btnBasculer.onclick = async () => {
      _captureFacing = _captureFacing === "environment" ? "user" : "environment";
      try{
        const nouveauFlux = await navigator.mediaDevices.getUserMedia(_captureContraintes(kind, _captureFacing));
        if(_captureStream) _captureStream.getTracks().forEach(t => t.stop());
        _captureStream = nouveauFlux;
        video.srcObject = nouveauFlux;
      }catch(err){
        erreur.style.display = "block";
        erreur.textContent = tj("engine/static/engine/js/client_1.js:408:31") + (err && err.message ? err.message : err);
        _captureFacing = _captureFacing === "environment" ? "user" : "environment";
      }
    };

    btnReprendre.onclick = async () => {
      _captureBlob = null; _captureChunks = [];
      photoApercu.style.display = "none"; audioApercu.style.display = "none"; videoApercu.style.display = "none";
      btnUtiliser.style.display = "none"; btnReprendre.style.display = "none";
      btnAction.style.display = "inline-block";
      btnAction.textContent = kind === "image" ? "📸 Capturer" : tj("engine/static/engine/js/client_1.js:418:66");
      if(!_captureStream){
        try{
          _captureStream = await navigator.mediaDevices.getUserMedia(_captureContraintes(kind, _captureFacing));
        }catch(err){ erreur.style.display="block"; erreur.textContent = tj("engine/static/engine/js/client_1.js:422:74"); return; }
      }
      if(kind === "image" || kind === "video"){ video.srcObject = _captureStream; video.style.display = "block"; btnBasculer.style.display = "flex"; }
    };

    btnUtiliser.onclick = () => {
      if(!_captureBlob) return;
      const ext = kind === "image" ? "jpg" : "webm";
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
      btnAction.onclick = () => {
        if(!enregistrementEnCours){
          enregistrementEnCours = true;
          _captureChunks = [];
          try{
            _captureRecorder = new MediaRecorder(_captureStream);
          }catch(e){
            erreur.style.display = "block"; erreur.textContent = tj("engine/static/engine/js/client_1.js:456:67");
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
          btnAction.textContent = tj("engine/static/engine/js/client_1.js:470:36");
          btnBasculer.style.display = "none";
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

function creerWidget(champ) {
  const t = champ.type;
  if (t === 'fichier' || t === 'image' || t === 'son' || t === 'video') {
    const wrap = document.createElement('div');
    wrap.className = 'upload-zone';
    const input = document.createElement('input');
    input.type = 'file'; input.id = 'champ-' + champ.slug;
    if (t === 'image') input.accept = 'image/*';
    if (t === 'son') input.accept = 'audio/*';
    if (t === 'video') input.accept = 'video/*';
    const lbl2 = document.createElement('div');
    lbl2.textContent = t === 'image' ? tj("engine/static/engine/js/client_1.js:502:41") : t === 'son' ? tj("engine/static/engine/js/client_1.js:502:92") : t === 'video' ? tj("engine/static/engine/js/client_1.js:502:142") : tj("engine/static/engine/js/client_1.js:502:179");
    const nom = document.createElement('div');
    nom.className = 'upload-nom'; nom.id = 'nom-' + champ.slug;
    input.onchange = () => { if (input.files[0]) { fichiers[champ.slug] = input.files[0]; nom.textContent = input.files[0].name; } };
    wrap.onclick = () => input.click();
    wrap.appendChild(input); wrap.appendChild(lbl2); wrap.appendChild(nom);
    if (t === 'image' || t === 'son' || t === 'video') {
      const actions = document.createElement('div');
      actions.className = 'upload-actions';
      const btnCapture = document.createElement('button');
      btnCapture.type = 'button'; btnCapture.className = 'btn-capture-media';
      btnCapture.textContent = t === 'image' ? tj("engine/static/engine/js/client_1.js:513:49") : t === 'son' ? tj("engine/static/engine/js/client_1.js:513:88") : tj("engine/static/engine/js/client_1.js:513:113");
      btnCapture.onclick = async (e) => {
        e.stopPropagation();
        const fichier = await ouvrirCaptureMedia(t);
        if (fichier) { fichiers[champ.slug] = fichier; nom.textContent = fichier.name; }
      };
      actions.appendChild(btnCapture);
      wrap.appendChild(actions);
    }
    return wrap;
  }
  if (t === 'choix_multiple') {
    const wrap = document.createElement('div');
    wrap.className = 'choix-multiple'; wrap.id = 'champ-' + champ.slug;
    (champ.choix || '').split(',').map(s => s.trim()).filter(Boolean).forEach(opt => {
      const row = document.createElement('label');
      row.className = 'choix-item';
      const cb = document.createElement('input'); cb.type = 'checkbox'; cb.value = opt;
      row.appendChild(cb); row.appendChild(document.createTextNode(opt)); wrap.appendChild(row);
    });
    return wrap;
  }
  if (t === 'choix' || t === 'choix_unique') {
    const sel = document.createElement('select'); sel.className = 'champ'; sel.id = 'champ-' + champ.slug;
    const vide = document.createElement('option'); vide.value = ''; vide.textContent = tj("engine/static/engine/js/client_1.js:537:89"); sel.appendChild(vide);
    (champ.choix || '').split(',').map(s => s.trim()).filter(Boolean).forEach(opt => {
      const o = document.createElement('option'); o.value = opt; o.textContent = opt; sel.appendChild(o);
    });
    return sel;
  }
  if (t === 'booleen' || t === 'switch') {
    const sel = document.createElement('select'); sel.className = 'champ'; sel.id = 'champ-' + champ.slug;
    [['', tj("engine/static/engine/js/client_1.js:545:12")], ['true', tj("engine/static/engine/js/client_1.js:545:37")], ['false', tj("engine/static/engine/js/client_1.js:545:55")]].forEach(([v, l]) => {
      const o = document.createElement('option'); o.value = v; o.textContent = l; sel.appendChild(o);
    });
    return sel;
  }
  if (t === 'vecteur' || t === 'matrice') {   // même grille visuelle que côté agent ; le textarea masqué (id champ-<slug>) porte le JSON
    const w = document.createElement('div'); w.innerHTML = GYTenseur.html('champ-' + champ.slug, t, champ.choix || champ.options || ''); return w.firstElementChild;
  }
  if (t === 'texte_long') {
    const ta = document.createElement('textarea'); ta.className = 'champ'; ta.id = 'champ-' + champ.slug; ta.placeholder = champ.nom; return ta;
  }
  if (t === 'nombre' || t === 'entier' || t === 'decimal') {
    const inp = document.createElement('input'); inp.type = 'number'; inp.className = 'champ'; inp.id = 'champ-' + champ.slug; inp.placeholder = '0';
    if (t === 'entier') inp.step = '1'; return inp;
  }
  if (t === 'geolocalisation') {   // même champ que côté agent : saisie libre, carte (clic / recherche d'adresse) et position GPS
    const wrap = document.createElement('div');
    wrap.style.cssText = 'display:flex;flex-direction:column;gap:6px;width:100%;min-width:0';
    const inp = document.createElement('input'); inp.type = 'text'; inp.className = 'champ'; inp.id = 'champ-' + champ.slug;
    inp.placeholder = tj("engine/static/engine/js/index_1.js:geo:placeholder");
    const barre = document.createElement('div'); barre.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap';
    const bst = 'flex:1 1 140px;padding:7px 10px;font-size:13px;font-weight:600;cursor:pointer;white-space:normal';
    const bCarte = document.createElement('button'); bCarte.type = 'button'; bCarte.style.cssText = bst;
    bCarte.textContent = tj("engine/static/engine/js/index_1.js:geo:carte"); bCarte.title = tj("engine/static/engine/js/index_1.js:geo:carte:titre");
    bCarte.onclick = () => _choisirSurCarte(champ.slug, champ.id);
    const bGps = document.createElement('button'); bGps.type = 'button'; bGps.style.cssText = bst;
    bGps.textContent = tj("engine/static/engine/js/index_1.js:4832:318");
    bGps.onclick = () => _positionGPS(champ.slug);
    barre.appendChild(bCarte); barre.appendChild(bGps); wrap.appendChild(inp); wrap.appendChild(barre);
    return wrap;
  }
  if (t === 'date') { const inp = document.createElement('input'); inp.type = 'date'; inp.className = 'champ'; inp.id = 'champ-' + champ.slug; return inp; }
  if (t === 'heure') { const inp = document.createElement('input'); inp.type = 'time'; inp.className = 'champ'; inp.id = 'champ-' + champ.slug; return inp; }
  if (t === 'datetime') { const inp = document.createElement('input'); inp.type = 'datetime-local'; inp.className = 'champ'; inp.id = 'champ-' + champ.slug; return inp; }
  const inp = document.createElement('input');
  inp.type = t === 'email' ? 'email' : t === 'telephone' ? 'tel' : t === 'url' ? 'url' : 'text';
  inp.className = 'champ'; inp.id = 'champ-' + champ.slug; inp.placeholder = champ.nom;
  return inp;
}

// ── Soumission ───────────────────────────────────────────────────────────────
function valeurChamp(champ) {
  if ((champ.type === 'horodatage' || champ.type === 'id_client')) return null;   // ecrit par le serveur
  if (champ.type === 'fichier' || champ.type === 'image' || champ.type === 'son' || champ.type === 'video') return fichiers[champ.slug] || null;
  if (champ.type === 'choix_multiple') {
    const wrap = document.getElementById('champ-' + champ.slug);
    return [...wrap.querySelectorAll('input:checked')].map(cb => cb.value);
  }
  const el = document.getElementById('champ-' + champ.slug);
  return el ? el.value : '';
}

async function soumettre(e) {
  e.preventDefault();
  if (!tableActive) return;
  let valide = true;
  // Formulaire conditionnel : seuls les champs des sections traversées sont validés et envoyés (les autres restent vides).
  const champsEnvoyes = (window.gyFormCond && gyFormCond.actif(tableActive)) ? gyFormCond.champsActifs(tableActive) : tableActive.champs;
  champsEnvoyes.forEach(c => {
    const msg = document.getElementById('err-' + c.slug);
    const el = document.getElementById('champ-' + c.slug);
    if (msg) msg.textContent = '';
    if (el) el.classList.remove('erreur');
    if (c.obligatoire && c.type !== 'horodatage' && c.type !== 'id_client') {
      const val = valeurChamp(c);
      const vide = (val === null || val === '' || (Array.isArray(val) && val.length === 0));
      if (vide) { if (msg) msg.textContent = tj("engine/static/engine/js/client_1.js:590:47"); if (el) el.classList.add('erreur'); valide = false; }
    }
  });
  if (!valide) return;
  const btn = document.getElementById('btn-soumettre');
  btn.disabled = true; btn.textContent = tj("engine/static/engine/js/client_1.js:595:43");
  try {
    const aFichier = champsEnvoyes.some(c => (c.type === 'fichier' || c.type === 'image' || c.type === 'son' || c.type === 'video') && fichiers[c.slug]);
    let response;
    if (aFichier) {
      const fd = new FormData();
      champsEnvoyes.filter(c => c.type !== 'horodatage' && c.type !== 'id_client').forEach(c => {
        if (c.type === 'fichier' || c.type === 'image' || c.type === 'son' || c.type === 'video') { if (fichiers[c.slug]) fd.append(c.slug, fichiers[c.slug]); }
        else if (c.type === 'choix_multiple') { valeurChamp(c).forEach(v => fd.append(c.slug, v)); }
        else { fd.append(c.slug, valeurChamp(c)); }
      });
      response = await fetch(`/api/tables/${tableActive.id}/lignes/`, { method:'POST', headers:{'X-CSRFToken':getCsrf()}, body:fd });
    } else {
      const corps = {};
      champsEnvoyes.forEach(c => { if (c.type !== 'horodatage' && c.type !== 'id_client') corps[c.slug] = valeurChamp(c); });
      response = await fetch(`/api/tables/${tableActive.id}/lignes/`, { method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':getCsrf()}, body:JSON.stringify(corps) });
    }
    const data = await response.json();
    if (!response.ok) {
      if (response.status === 402) { _afficher402(data); }
      else { if (data.champs) { Object.entries(data.champs).forEach(([slug, texte]) => { const errEl = document.getElementById('err-'+slug); const inp = document.getElementById('champ-'+slug); if (errEl) errEl.textContent = texte; if (inp) inp.classList.add('erreur'); }); }
      toast(data.erreur || tj("engine/static/engine/js/client_1.js:616:29"), 'err'); }
    } else { afficherConfirmation(data); }
  } catch { toast(tj("engine/static/engine/js/client_1.js:618:20"), 'err'); }
  finally { btn.disabled = false; btn.textContent = tj("engine/static/engine/js/client_1.js:619:54"); }
}

function reinitialiser() {
  document.getElementById('le-form').querySelectorAll('.champ').forEach(el => { if (el.tagName==='SELECT') el.selectedIndex=0; else el.value=''; el.classList.remove('erreur'); });
  document.querySelectorAll('.choix-item input').forEach(cb => cb.checked = false);
  document.querySelectorAll('.upload-nom').forEach(el => el.textContent = '');
  document.querySelectorAll('.champ-erreur-msg').forEach(el => el.textContent = '');
  fichiers = {};
}

function afficherConfirmation(data) {
  document.getElementById('zone-formulaire').classList.remove('visible');

  const ligne = data?.ligne || {};
  const ligneId = ligne._id || data?.id;
  const tableId = tableActive?.id;

  // Référence et date
  const now = new Date();
  const dateStr = now.toLocaleString(window.I18N_LOCALE || (window.I18N_LOCALE||'fr-FR'), {day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
  document.getElementById('conf-ref-num').textContent = ligneId ? `#${ligneId}` : '—';
  document.getElementById('conf-ref-date').textContent = dateStr;

  // Récapitulatif — valeurs EXACTES de la ligne DB
  const lignes = document.getElementById('conf-recap-lignes');
  lignes.innerHTML = '';
  (tableActive?.champs || []).forEach(c => {
    const val = ligne[c.slug];
    if (val === undefined || val === null || val === '') return;
    const row = document.createElement('div');
    row.className = 'conf-ligne';
    const lbl = document.createElement('div'); lbl.className = 'conf-label'; lbl.textContent = c.nom;
    const v = document.createElement('div'); v.className = 'conf-valeur';
    if (['image', 'son', 'video', 'fichier'].includes(c.type)) {
      v.innerHTML = _renduValeurMedia(c, val);
    } else {
      v.textContent = Array.isArray(val) ? val.join(', ') : String(val);
    }
    row.appendChild(lbl); row.appendChild(v); lignes.appendChild(row);
  });

  // Footer print
  document.getElementById('conf-print-footer').textContent =
    tj("engine/static/engine/js/client_1.js:664:6", {v1: ligneId, v2: tableActive?.nom || '', v3: dateStr});

  // Bouton PDF → URL directe
  const btnPdf = document.getElementById('btn-pdf');
  if (ligneId && tableId) {
    btnPdf.onclick = () => gyTelecharger(`/api/tables/${tableId}/lignes/${ligneId}/recu-pdf/`, `recu_${tableId}_${ligneId}.pdf`,
      { bouton: btnPdf, succes: () => toast(tj("engine/static/engine/js/client_1.js:670:46"), 'ok') });
    btnPdf.style.display = '';
  } else {
    btnPdf.style.display = 'none';
  }

  document.getElementById('confirmation').classList.add('visible');
}

function remplirNouveau() {
  document.getElementById('confirmation').classList.remove('visible');
  if (tableActive) ouvrirTable(tableActive.id);
}

function getCsrf() {
  return document.cookie.split(';').map(c => c.trim()).find(c => c.startsWith('gy_csrftoken='))?.split('=')[1] || '';
}
function toast(msg, type) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.className = type + ' show';
  clearTimeout(t._timer); t._timer = setTimeout(() => t.className = '', 3000);
}

if (TABLES.length === 1) ouvrirTable(TABLES[0].id);
// Lien direct depuis une publicité : /espace-client/?table=<id> ouvre ce formulaire (ignoré si la table n'est plus dans le menu du client).
try {
  const _tid = parseInt(new URLSearchParams(location.search).get('table'), 10);
  if (_tid && TABLES.some(t => t.id === _tid)) ouvrirTable(_tid);
} catch (e) { /* lien invalide : on reste sur l'accueil */ }

// ── Message solde insuffisant (402) ─────────────────────────────────────────
function _afficher402(data) {
  const msg = data && data.erreur ? data.erreur : tj("engine/static/engine/js/client_1.js:697:52");
  // Cherche un conteneur dédié dans la page, sinon toast + redirect
  const el = document.getElementById('msg-402');
  if (el) {
    el.style.display = 'block';
    el.querySelector('.msg402-texte').textContent = msg;
    el.querySelector('.msg402-lien').href = '/espace-client/paiement/' + (data.service ? '?service=' + data.service : '');
  } else {
    // Pas de div dédiée — on affiche un toast et on propose la redirection après 3s
    toast('💳 ' + msg + tj("engine/static/engine/js/client_1.js:706:25"), 'err');
    setTimeout(async () => { if (await GY.confirmer(msg + tj("engine/static/engine/js/client_1.js:707:43"))) location.href = '/espace-client/paiement/' + (data.service ? '?service=' + data.service : ''); }, 300);
  }
}
