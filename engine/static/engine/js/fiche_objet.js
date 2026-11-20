// Fiche objet consolidée — cahier des charges Ontology, section 10
// ("Applications object-centric") : bouton "🗂 Fiche" réutilisable depuis
// n'importe quelle interface (grille, dashboard BI...), pas seulement
// l'onglet dédié de la page Ontologie. Appelle l'API déjà existante
// (api/ontologie/fiche/, voir engine/ontologie_fiche.py) — aucune logique
// dupliquée, seulement le rendu.
//
// Auto-suffisant (getCookie/fetch propres) car chargé sur des pages qui
// n'ont pas toutes le même helper `api()` global (index.html en a un,
// dashboard_bi.html le redéfinit localement, ontologie.html non) ; le
// modal est injecté en styles inline pour ne dépendre d'aucune règle CSS
// scopée par page (voir app.css : .modal-fond est scopé par body.page-*).

function _ficheGetCookie(nom) {
  const valeur = document.cookie.match("(^|;)\\s*" + nom + "\\s*=\\s*([^;]+)");
  return valeur ? valeur.pop() : "";
}

function _ficheAssurerModal() {
  if (document.getElementById("modal-fiche-objet-partagee")) return;
  const fond = document.createElement("div");
  fond.id = "modal-fiche-objet-partagee";
  fond.style.cssText = "position:fixed;inset:0;background:color-mix(in srgb,var(--voile,#000000) 75%,transparent);display:none;"
    + "align-items:center;justify-content:center;z-index:3000;padding:16px";
  fond.innerHTML = `
    <div style="background:var(--fond-info,#1e2530);color:var(--texte-clair,#e5e7eb);border-radius:10px;max-width:560px;width:100%;
                max-height:85vh;overflow-y:auto;padding:20px 22px;font-family:inherit">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <h3 style="margin:0;font-size:16px">${tj("engine/static/engine/js/fiche_objet.js:29:45")}</h3>
        <button onclick="document.getElementById('modal-fiche-objet-partagee').style.display='none'"
                style="background:none;border:none;color:var(--texte-discret,#9ca3af);font-size:20px;cursor:pointer;line-height:1">×</button>
      </div>
      <div id="fiche-objet-partagee-corps" style="font-size:13px"></div>
    </div>`;
  fond.addEventListener("click", (e) => { if (e.target === fond) fond.style.display = "none"; });
  document.body.appendChild(fond);
}

function _ficheLigneHTML(cle, valeur) {
  return `<div style="display:flex;justify-content:space-between;gap:10px;padding:3px 0;
                       border-bottom:1px solid color-mix(in srgb,var(--voile-clair,#ffffff) 6%,transparent)">
            <span style="color:var(--texte-discret,#9ca3af)">${cle}</span><b>${valeur}</b></div>`;
}

function _ficheTitreHTML(texte) {
  return `<div style="font-weight:600;margin:14px 0 6px;font-size:12px;
                       text-transform:uppercase;letter-spacing:.03em;color:var(--texte-discret,#9ca3af)">${texte}</div>`;
}

function _ficheEchapper(t) {
  return String(t ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}

// Section « Aller à » : destinations liées à l'objet (portail de navigation, engine/portail.py).
// Les liens sont déjà vérifiés côté serveur ; ici on ne fait que les afficher, groupés par catégorie.
function _ficheAllerAHTML(portail) {
  if (!portail || !portail.supporte) return "";
  const dests = portail.destinations || [];
  let html = _ficheTitreHTML(tj("engine/static/engine/js/fiche_objet.js:59:31", {v1: dests.length}));
  if (!dests.length) {
    html += `<div style="color:var(--texte-discret,#9ca3af);font-size:12px">${tj("engine/static/engine/js/fiche_objet.js:61:57")}</div>`;
  } else {
    const groupes = {};
    for (const x of dests) (groupes[x.categorie] = groupes[x.categorie] || []).push(x);
    for (const [cat, liste] of Object.entries(groupes)) {
      html += `<div style="color:var(--texte-discret,#9ca3af);font-size:11px;margin:8px 0 2px">${_ficheEchapper(cat)}</div>`;
      html += liste.map(x => `<a href="${_ficheEchapper(x.url)}" style="display:flex;justify-content:space-between;
          gap:10px;padding:5px 8px;margin:2px 0;border-radius:6px;background:color-mix(in srgb,var(--voile-clair,#ffffff) 5%,transparent);
          color:var(--texte-lien,#93c5fd);text-decoration:none">
          <span>${_ficheEchapper(x.label)}</span>
          <span style="color:var(--texte-discret,#9ca3af);font-size:11px">${_ficheEchapper(x.via ? "via " + x.via : x.relation)}</span></a>`).join("");
    }
  }
  if (portail.ontologie_url) {
    html += `<a href="${_ficheEchapper(portail.ontologie_url)}" style="display:block;margin-top:8px;color:var(--texte-discret,#9ca3af);
        font-size:12px;text-decoration:none">${tj("engine/static/engine/js/fiche_objet.js:76:47")}</a>`;
  }
  const fantomes = portail.liens_fantomes || [];
  if (fantomes.length) {
    html += _ficheTitreHTML(tj("engine/static/engine/js/fiche_objet.js:80:30", {v1: fantomes.length}));
    html += fantomes.map(f => `<div style="color:var(--texte-orange,#fbbf24);font-size:12px;padding:2px 0">⚠ ${_ficheEchapper(f.raison)}</div>`).join("");
  }
  return html;
}

async function ouvrirFicheObjet(noeudId, pageId) {
  _ficheAssurerModal();
  const fond = document.getElementById("modal-fiche-objet-partagee");
  const corps = document.getElementById("fiche-objet-partagee-corps");
  corps.innerHTML = tj("engine/static/engine/js/fiche_objet.js:90:22");
  fond.style.display = "flex";

  // Sur la page d'ontologie, la fiche doit être calculée sur le MÊME graphe que celui affiché (page_id).
  if (pageId === undefined && typeof PAGE_ID !== "undefined") pageId = PAGE_ID;

  let d;
  let reponse;
  try {
    reponse = await fetch("/api/ontologie/fiche/", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-CSRFToken": _ficheGetCookie("gy_csrftoken")},
      body: JSON.stringify({noeud_id: noeudId, page_id: pageId || undefined}),
    });
  } catch (e) {
    corps.innerHTML = `<div style="color:var(--texte-erreur,#f87171)">${tj("engine/static/engine/js/fiche_objet.js:102:52")}</div>`;
    return;
  }
  const texte = await reponse.text();
  try {
    d = JSON.parse(texte);
  } catch (e) {
    // Le serveur a répondu, mais pas en JSON valide : on montre le vrai statut au lieu d'« erreur réseau ».
    corps.innerHTML = `<div style="color:var(--texte-erreur,#f87171)">${tj("engine/static/engine/js/fiche_objet.js:113:71", {v1: reponse.status, v2: _ficheEchapper(texte.slice(0, 300))})}</div>`;
    return;
  }
  if (d.erreur) {
    corps.innerHTML = `<div style="color:var(--texte-erreur,#f87171)">${_ficheEchapper(d.erreur)}</div>`;
    return;
  }

  const o = d.objet;
  let html = `<div style="font-size:15px;font-weight:600">${o.label} <span style="color:var(--texte-discret,#9ca3af);font-weight:400">(${o.type})</span></div>`;

  const proprietes = Object.entries(o).filter(([k]) => !["id", "type", "label", "slug", "couleur", "taille"].includes(k));
  if (proprietes.length) {
    html += _ficheTitreHTML(tj("engine/static/engine/js/fiche_objet.js:115:30"));
    for (const [k, v] of proprietes) html += _ficheLigneHTML(k, v);
  }

  html += _ficheAllerAHTML(d.portail);

  html += _ficheTitreHTML(tj("engine/static/engine/js/fiche_objet.js:lbl_relations", {v1: d.relations.total ?? 0}));
  html += (d.relations.noeuds || []).length
    ? d.relations.noeuds.map(n => _ficheLigneHTML(n.type, n.label)).join("")
    : `<div style="color:var(--texte-discret,#9ca3af);font-size:12px">${tj("engine/static/engine/js/fiche_objet.js:124:51")}</div>`;

  html += _ficheTitreHTML(tj("engine/static/engine/js/fiche_objet.js:lbl_actions_dispo", {v1: d.actions_disponibles.length}));
  html += d.actions_disponibles.length
    ? d.actions_disponibles.map(a => `<div style="padding:3px 0" title="${a.description || ""}">• ${a.nom}</div>`).join("")
    : `<div style="color:var(--texte-discret,#9ca3af);font-size:12px">${tj("engine/static/engine/js/fiche_objet.js:129:51")}</div>`;

  if (d.documents.length) {
    html += _ficheTitreHTML(`${tj("engine/static/engine/js/fiche_objet.js:143:30", {v1: d.documents.length})}`);
    html += d.documents.map(doc => _ficheLigneHTML(doc.titre || doc.type_document, doc.statut)).join("");
  }

  if (d.activite_recente.length) {
    html += _ficheTitreHTML(tj("engine/static/engine/js/fiche_objet.js:137:30"));
    html += d.activite_recente.map(a => {
      const quand = new Date(a.horodatage).toLocaleString();
      const valeur = a.resultat === "echec" ? `⚠ ${quand}` : quand;
      return _ficheLigneHTML(a.action, valeur);
    }).join("");
  }

  corps.innerHTML = html;
}
