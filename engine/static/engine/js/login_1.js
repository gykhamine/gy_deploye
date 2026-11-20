// Connexion en deux temps (gabarit : templates/engine/login.html).
//  Étape 1 — aucune entreprise choisie : liste (logo, nom, couleur du thème) avec barre de recherche. Un clic ouvre /login/?e=<slug> :
//            le serveur retient l'entreprise et la page se recharge à ses couleurs (thème, logo, nom, langue) dès l'écran de connexion.
//  Étape 2 — entreprise choisie : identifiants (formulaire rendu par le serveur) + lien « Connexion client » de cette entreprise.
// Tous les textes passent par tj() (catalogues i18n) ; tout ce qui vient du serveur est posé par le DOM (textContent), jamais en HTML.

const COULEUR_HEX = /^#[0-9a-f]{6}$/i;

function urlSure(u){
  u = String(u || "");
  return (u.startsWith("/") && !u.startsWith("//")) || u.startsWith("https://") ? u : "";
}

// ── Étape 1 : choix de l'entreprise ──────────────────────────────────────────
function creerEntreeEntreprise(e, suite){
  const a = document.createElement("a");
  a.className = "ent-item";
  const lien = String(e.lien_connexion || "");
  a.href = (lien.startsWith("/login/") ? lien : "#") + suite;
  const accent = COULEUR_HEX.test(e.couleur || "") ? e.couleur : "";
  const fond = COULEUR_HEX.test(e.fond || "") ? e.fond : "";
  if(accent) a.style.setProperty("--ent-accent", accent);
  if(fond) a.style.setProperty("--ent-fond", fond);

  const nom = String(e.nom || "").trim();
  const logo = document.createElement("span");
  logo.className = "ent-logo";
  const initiale = () => {   // pas de logo (ou logo introuvable) : l'initiale aux couleurs du thème de l'entreprise
    logo.textContent = (Array.from(nom)[0] || "?").toUpperCase();
    if(accent) logo.style.background = accent;
    if(COULEUR_HEX.test(e.texte_couleur || "")) logo.style.color = e.texte_couleur;
  };
  const src = urlSure(e.logo_url);
  if(src){
    const img = document.createElement("img");
    img.alt = "";
    img.loading = "lazy";
    img.onerror = () => { img.remove(); initiale(); };
    img.src = src;
    logo.appendChild(img);
  }else{
    initiale();
  }

  const libelle = document.createElement("span");
  libelle.className = "ent-nom";
  libelle.textContent = nom;

  const pastille = document.createElement("span");   // aperçu du thème : fond + couleur d'accent de l'entreprise
  pastille.className = "ent-pastille";
  pastille.setAttribute("aria-hidden", "true");

  a.append(logo, libelle, pastille);
  return a;
}

function initialiserChoixEntreprise(){
  const zone = document.getElementById("choix-entreprise");
  const champ = document.getElementById("recherche-entreprise");
  const liste = document.getElementById("liste-entreprises");
  const statut = document.getElementById("statut-entreprises");
  if(!zone || !champ || !liste || !statut) return;
  const brut = zone.dataset.suite || "";
  const suite = brut.startsWith("/") && !brut.startsWith("//") ? "&next=" + encodeURIComponent(brut) : "";
  let minuteur = null, controle = null;

  async function chercher(q){
    if(controle) controle.abort();   // une frappe plus récente remplace la recherche en cours
    controle = new AbortController();
    try{
      const r = await fetch("/api/entreprise/connexion/liste/?q=" + encodeURIComponent(q), {signal: controle.signal, credentials: "same-origin"});
      if(!r.ok) throw new Error("http");
      const data = await r.json();
      const entreprises = (data.entreprises || []).filter(e => e && e.slug);
      liste.replaceChildren(...entreprises.map(e => {
        const li = document.createElement("li");
        li.appendChild(creerEntreeEntreprise(e, suite));
        return li;
      }));
      if(!entreprises.length){
        statut.textContent = tj(q ? "engine/static/engine/js/login_1.js:aucun_resultat" : "engine/static/engine/js/login_1.js:aucune_entreprise");
      }else{
        statut.textContent = data.plus ? tj("engine/static/engine/js/login_1.js:affiner_recherche") : "";
      }
    }catch(ex){
      if(ex && ex.name === "AbortError") return;
      liste.replaceChildren();
      statut.textContent = tj("engine/static/engine/js/login_1.js:erreur_liste");
    }
  }

  champ.addEventListener("input", () => {
    clearTimeout(minuteur);
    minuteur = setTimeout(() => chercher(champ.value.trim()), 250);   // pas une requête par touche
  });
  champ.addEventListener("keydown", ev => {
    const premier = liste.querySelector(".ent-item");
    if(ev.key === "Enter"){ ev.preventDefault(); if(premier) premier.click(); }   // Entrée : la première entreprise trouvée
    else if(ev.key === "ArrowDown" && premier){ ev.preventDefault(); premier.focus(); }
  });
  liste.addEventListener("keydown", ev => {   // flèches : de proche en proche dans la liste, retour à la recherche en haut
    if(ev.key !== "ArrowDown" && ev.key !== "ArrowUp") return;
    const items = Array.from(liste.querySelectorAll(".ent-item"));
    const i = items.indexOf(document.activeElement);
    if(i < 0) return;
    ev.preventDefault();
    if(ev.key === "ArrowDown") (items[i + 1] || items[i]).focus();
    else (i > 0 ? items[i - 1] : champ).focus();
  });
  chercher("");
}

// ── Étape 2 : lien « Connexion client » de l'entreprise choisie ──────────────
async function chargerLienClient(){
  const zone = document.getElementById("section-ent");
  if(!zone) return;
  try{
    const r = await fetch("/api/entreprise/publique/liste/", {credentials: "same-origin"});
    if(!r.ok) return;
    const data = await r.json();
    const e = (data.entreprises || []).find(x => x && x.slug === data.courante);   // seule l'entreprise choisie, et si elle ouvre l'accès client
    if(!e) return;
    const a = document.createElement("a");
    a.className = "btn-client";
    a.href = String(e.lien_client || "").startsWith("/") ? e.lien_client : "#";
    // Nom créé automatiquement (« Entreprise de … ») : jamais affiché ; sans nom, le bouton dit seulement « Connexion client ».
    const nom = String(e.nom || "").trim();
    const libelle = tj("engine/static/engine/js/login_1.js:14:24");
    a.textContent = (!nom || nom.startsWith(tj("engine/static/engine/js/login_1.js:prefixe_nom_auto"))) ? libelle.replace(/[\s\u2014\u2013-]+$/, "") : libelle + nom;
    zone.replaceChildren(a);
  }catch(ex){}
}

initialiserChoixEntreprise();
chargerLienClient();
