// Connexion à une base de données externe + groupes de tables : interface des endpoints
// /api/connexions-externes/… et /api/groupes-tables/… (réutilise api, notifier*, escapeHtml,
// _pollerImport et rafraichirTables de index_1.js — aucun suivi d'import dupliqué).
(() => {
  const E = escapeHtml;
  const CHAMP = (id, libelle, extra = "") => `<div class="champ-item"><label>${libelle}</label><input id="${id}" ${extra}></div>`;
  const occupe = async (b, fn) => { b.disabled = true; try { await fn(); } catch(e) { /* déjà notifié par api() */ } finally { b.disabled = false; } };

  function ouvrirFenetre(titre, html){
    const fond = document.createElement("div");
    fond.style.cssText = "position:fixed;inset:0;background:color-mix(in srgb,var(--voile,#000000) 70%,transparent);display:flex;align-items:center;justify-content:center;z-index:2000;padding:14px";
    fond.innerHTML = `<div style="width:600px;max-width:100%;background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:18px;max-height:90vh;overflow-y:auto">
      <h2 style="margin:0 0 12px;font-size:15px">${titre}</h2>${html}
      <div class="modal-actions"><button data-fermer>${tj("engine/static/engine/js/index_7.js:14:55")}</button></div></div>`;
    fond.onclick = (e) => { if(e.target === fond || e.target.hasAttribute("data-fermer")) fond.remove(); };
    document.body.appendChild(fond);
    return fond;
  }

  // ── Base de données externe ──────────────────────────────────────────────
  async function ouvrirConnexions(){
    const {connexions, moteurs} = await api("/api/connexions-externes/");
    const f = ouvrirFenetre(tj("engine/static/engine/js/index_7.js:23:30"), `
      <div class="champ-item" style="margin-bottom:10px"><label>${tj("engine/static/engine/js/index_7.js:24:66")}</label><select id="ce-choix"></select></div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px">
        ${CHAMP("ce-nom", tj("engine/static/engine/js/index_7.js:26:28"), tj("engine/static/engine/js/index_7.js:26:35"))}
        <div class="champ-item"><label>${tj("engine/static/engine/js/index_7.js:27:41")}</label><select id="ce-moteur">${moteurs.map(m => `<option value="${E(m.id)}">${E(m.libelle)}</option>`).join("")}</select></div>
        ${CHAMP("ce-hote", tj("engine/static/engine/js/index_7.js:28:29"), 'placeholder="ex : 192.168.1.20"')}
        ${CHAMP("ce-port", "Port", 'type="number" min="1" max="65535"')}
        ${CHAMP("ce-base", tj("engine/static/engine/js/index_7.js:30:29"))}
        ${CHAMP("ce-user", tj("engine/static/engine/js/index_7.js:31:29"), 'autocomplete="off"')}
        ${CHAMP("ce-mdp", tj("engine/static/engine/js/index_7.js:32:28"), 'type="password" autocomplete="new-password"')}
        <label style="display:flex;align-items:center;gap:6px;font-size:12px;align-self:end;padding-bottom:6px"><input id="ce-ssl" type="checkbox" style="width:auto"> ${tj("engine/static/engine/js/index_7.js:33:169")}</label>
      </div>
      <div class="modal-actions" style="justify-content:flex-start">
        <button class="principal" id="ce-enregistrer">${tj("engine/static/engine/js/index_7.js:36:56")}</button>
        <button id="ce-supprimer" style="display:none;border:1px solid var(--danger);color:var(--danger);background:transparent">${tj("engine/static/engine/js/index_7.js:37:131")}</button>
      </div>
      <div id="ce-import" style="display:none;margin-top:14px;border-top:1px solid var(--border);padding-top:12px">
        <div class="champ-item"><label>${tj("engine/static/engine/js/index_7.js:40:41")}</label>
          <div style="display:flex;gap:8px;flex-wrap:wrap"><select id="ce-table" style="flex:1;min-width:160px"></select><button id="ce-lister">${tj("engine/static/engine/js/index_7.js:41:146")}</button></div></div>
        <div id="ce-apercu" style="overflow-x:auto;margin-top:8px"></div>
        <div class="modal-actions" style="justify-content:flex-start"><button id="ce-voir" disabled>${tj("engine/static/engine/js/index_7.js:43:102")}</button><button class="principal" id="ce-importer" disabled>${tj("engine/static/engine/js/index_7.js:43:169")}</button></div>
        <div style="font-size:11px;color:var(--muted)">${tj("engine/static/engine/js/index_7.js:44:57")}</div>
      </div>`);
    const $ = (id) => f.querySelector("#" + id);
    let liste = connexions, courante = null;
    const moteurPar = (id) => moteurs.find(m => m.id === id);
    const majPort = () => { $("ce-port").placeholder = tj("engine/static/engine/js/index_7.js:49:57") + moteurPar($("ce-moteur").value).port; };
    const remplirChoix = () => {
      $("ce-choix").innerHTML = `<option value="">${tj("engine/static/engine/js/index_7.js:51:52")}</option>` + liste.map(c => `<option value="${c.id}">${E(c.nom)} — ${E(c.moteur_libelle)}</option>`).join("");
      $("ce-choix").value = courante ? String(courante.id) : "";
    };
    const choisir = (c) => {
      courante = c;
      $("ce-nom").value = c ? c.nom : ""; $("ce-moteur").value = c ? c.moteur : moteurs[0].id;
      $("ce-hote").value = c ? c.hote : ""; $("ce-port").value = c && c.port ? c.port : "";
      $("ce-base").value = c ? c.nom_base : ""; $("ce-user").value = c ? c.utilisateur : "";
      $("ce-mdp").value = ""; $("ce-mdp").placeholder = c && c.mot_de_passe_renseigne ? tj("engine/static/engine/js/index_7.js:59:90") : "";
      $("ce-ssl").checked = !!(c && c.ssl_actif);
      $("ce-supprimer").style.display = $("ce-import").style.display = c ? "" : "none";
      $("ce-table").innerHTML = ""; $("ce-apercu").innerHTML = ""; $("ce-voir").disabled = $("ce-importer").disabled = true;
      majPort(); remplirChoix();
    };
    $("ce-moteur").onchange = majPort;
    $("ce-choix").onchange = () => choisir(liste.find(c => String(c.id) === $("ce-choix").value) || null);
    $("ce-enregistrer").onclick = (ev) => occupe(ev.currentTarget, async () => {
      const corps = {nom: $("ce-nom").value.trim(), moteur: $("ce-moteur").value, hote: $("ce-hote").value.trim(),
        port: $("ce-port").value ? parseInt($("ce-port").value, 10) : null, nom_base: $("ce-base").value.trim(),
        utilisateur: $("ce-user").value, mot_de_passe: $("ce-mdp").value, ssl_actif: $("ce-ssl").checked};
      const url = "/api/connexions-externes/" + (courante ? courante.id + "/" : "");
      const res = await api(url, {method: courante ? "PATCH" : "POST", body: JSON.stringify(corps)});
      liste = (await api("/api/connexions-externes/")).connexions;
      choisir(liste.find(c => c.id === res.connexion.id));
      await api("/api/connexions-externes/" + courante.id + "/tester/", {method: "POST"});
      notifierSucces(tj("engine/static/engine/js/index_7.js:76:23"));
    });
    $("ce-supprimer").onclick = (ev) => occupe(ev.currentTarget, async () => {
      if(!await GY.confirmer(tj("engine/static/engine/js/index_7.js:79:20"))) return;
      await api("/api/connexions-externes/" + courante.id + "/", {method: "DELETE"});
      liste = liste.filter(c => c.id !== courante.id); choisir(null);
    });
    $("ce-lister").onclick = (ev) => occupe(ev.currentTarget, async () => {
      const {tables: noms} = await api("/api/connexions-externes/" + courante.id + "/tables/");
      $("ce-table").innerHTML = noms.map(n => `<option>${E(n)}</option>`).join("");
      $("ce-voir").disabled = $("ce-importer").disabled = !noms.length;
      if(!noms.length) notifierErreur(tj("engine/static/engine/js/index_7.js:87:40"));
    });
    $("ce-voir").onclick = (ev) => occupe(ev.currentTarget, async () => {
      const r = await api("/api/connexions-externes/" + courante.id + "/apercu/?table=" + encodeURIComponent($("ce-table").value));
      const cel = (t, v) => `<${t} style="text-align:left;padding:2px 8px;white-space:nowrap;border-bottom:1px solid var(--border)">${E(v)}</${t}>`;
      $("ce-apercu").innerHTML = `<table style="font-size:11px;border-collapse:collapse"><tr>${r.colonnes.map(c => cel("th", c)).join("")}</tr>` +
        r.lignes.slice(0, 5).map(l => `<tr>${l.map(v => cel("td", v)).join("")}</tr>`).join("") + "</table>";
    });
    $("ce-importer").onclick = (ev) => occupe(ev.currentTarget, async () => {
      const res = await api("/api/connexions-externes/" + courante.id + "/importer/", {method: "POST", body: JSON.stringify({table: $("ce-table").value})});
      f.remove();
      await _pollerImport(res.tache_id, res.nom_fichier);
    });
    choisir(null);
  }

  // Suppression en masse (table ou groupe + toutes ses données). Petit volume : déjà faite à la réponse. Gros volume : le serveur rend une
  // tâche de fond, suivie ici comme un import (même route de statut). Retourne true si tout est supprimé ; les erreurs sont signalées ici.
  window.gySupprimerEnMasse = async (url, options, nom) => {
    try {
      const r = await api(url, options);
      if(r.tache_id){
        notifier(tj("suppression.encours", {v1: nom}), "info");
        let fini = false;
        for(let i = 0; i < 5400 && !fini; i++){   // 3 h de suivi au plus ; la suppression, elle, continue côté serveur
          await new Promise(res => setTimeout(res, 2000));
          let d; try { d = await api("/api/import/" + r.tache_id + "/statut/"); } catch(e) { continue; }
          if(!d.fini) continue;
          if(d.statut !== "termine"){ notifierErreur(d.erreur || tj("suppression.echec")); return false; }
          fini = true;
        }
        if(!fini){ notifier(tj("suppression.suivi_interrompu"), "info"); return false; }
      }
      notifierSucces(tj("suppression.ok", {v1: nom}));
      return true;
    } catch(e) { return false; }   // api() a déjà signalé l'erreur
  };

  // ── Découper une table en plusieurs parties nommées (POST /api/tables/<id>/decouper/) ──
  async function ouvrirDecoupage(t, apres){
    let info;
    try { info = await api("/api/tables/" + t.id + "/decouper/"); } catch(e) { return; }
    if(!info.decoupable){ notifierErreur(tj("decoupage.ui.indisponible", {v1: info.raison || ""})); return; }
    const total = info.nb_lignes;
    const f = ouvrirFenetre(tj("decoupage.ui.titre", {v1: E(info.nom)}), `
      <p style="color:var(--muted);margin:0 0 10px;font-size:12px">${tj("decoupage.ui.intro", {v1: total})}</p>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px">
        <label style="font-size:12px">${tj("decoupage.ui.nb_parties")}</label>
        <input id="dc-n" type="number" min="2" max="${info.parties_max}" value="2" style="width:70px">
        <button data-act="egal" style="padding:3px 8px;font-size:11px">${tj("decoupage.ui.repartir")}</button>
      </div>
      <div style="display:grid;grid-template-columns:1fr 110px;gap:6px;font-size:11px;color:var(--muted);text-transform:uppercase"><span>${tj("decoupage.ui.col_nom")}</span><span>${tj("decoupage.ui.col_lignes")}</span></div>
      <div id="dc-parties"></div>
      <div id="dc-somme" style="font-size:12px;margin:8px 0"></div>
      <label style="display:flex;gap:8px;align-items:center;font-size:12px;margin-bottom:8px"><input id="dc-grouper" type="checkbox" style="width:auto"> ${tj("decoupage.ui.grouper")}</label>
      <div class="modal-actions" style="justify-content:flex-start"><button class="principal" data-act="valider">${tj("decoupage.ui.valider")}</button></div>`);
    const zone = f.querySelector("#dc-parties"), somme = f.querySelector("#dc-somme");
    const lire = () => [...zone.querySelectorAll("[data-nom-partie]")].map((el, i) => ({nom: el.value, nb_lignes: parseInt(zone.querySelectorAll("[data-nb-partie]")[i].value, 10) || 0}));
    const maj = () => { const s = lire().reduce((a, p) => a + p.nb_lignes, 0); somme.textContent = tj("decoupage.ui.somme", {v1: s, v2: total}); somme.style.color = s === total ? "var(--text)" : "var(--danger)"; };
    const egal = () => {
      const n = Math.max(2, Math.min(info.parties_max, parseInt(f.querySelector("#dc-n").value, 10) || 2));
      const anciens = lire().map(p => p.nom), base = Math.floor(total / n), reste = total % n;
      zone.innerHTML = Array.from({length: n}, (_, i) => `<div style="display:grid;grid-template-columns:1fr 110px;gap:6px;margin-top:4px"><input data-nom-partie value="${E(anciens[i] || (info.nom + " (" + (i + 1) + "/" + n + ")"))}" maxlength="120"><input data-nb-partie type="number" min="1" value="${base + (i < reste ? 1 : 0)}"></div>`).join("");
      maj();
    };
    zone.oninput = maj;
    f.querySelector("#dc-n").onchange = egal;
    egal();
    f.addEventListener("click", (ev) => {   // addEventListener : f.onclick écraserait la fermeture (fond, Fermer) posée par ouvrirFenetre
      const b = ev.target.closest("[data-act]"); if(!b) return;
      if(b.dataset.act === "egal"){ egal(); return; }
      if(b.dataset.act === "valider") occupe(b, async () => {
        const r = await api("/api/tables/" + t.id + "/decouper/", {method: "POST", body: JSON.stringify({parties: lire(), grouper: f.querySelector("#dc-grouper").checked})});
        notifierSucces(tj("decoupage.ui.ok", {v1: r.tables.length}));
        f.remove();
        if(apres) await apres();
      });
    });
  }

  window.gyDecouperTable = (t, apres) => ouvrirDecoupage(t, apres);   // utilisé par le bouton « ✂ Découper » de l'onglet Actions (sidebar)

  // ── Groupes de tables ────────────────────────────────────────────────────
  // Renommer un groupe ET toutes ses tables d'un coup : une fenêtre, un champ par table (plus besoin d'ouvrir chaque table pour la renommer).
  // Le groupe d'abord (le serveur renomme alors ses parties à sa suite), puis chaque table que l'utilisateur a modifiée elle-même.
  async function ouvrirRenommageGroupe(g, apres){
    const f = ouvrirFenetre(tj("groupes.renommer_parties.titre"), `
      <input data-groupe value="${E(g.nom)}" style="width:100%;box-sizing:border-box;margin-bottom:10px;font-weight:600">
      ${g.tables.map(t => `<input data-table="${t.id}" data-nom="${E(t.nom)}" value="${E(t.nom)}" style="width:100%;box-sizing:border-box;margin-bottom:6px">`).join("")}
      <div style="margin-top:8px"><button class="principal" data-act="ok">${tj("groupes.renommer_parties.enregistrer")}</button></div>`);
    f.querySelector("[data-act=ok]").onclick = (ev) => occupe(ev.currentTarget, async () => {
      const actuel = new Map(g.tables.map(t => [String(t.id), t.nom]));
      const nomG = f.querySelector("[data-groupe]").value.trim();
      if(nomG && nomG !== g.nom){
        const r = await api("/api/groupes-tables/" + g.id + "/", {method: "PATCH", body: JSON.stringify({nom: nomG})});
        for(const t of r.tables_renommees || []) actuel.set(String(t.id), t.nom);
      }
      let n = 0;
      for(const i of f.querySelectorAll("[data-table]")){
        const v = i.value.trim();
        if(v && v !== i.dataset.nom && v !== actuel.get(i.dataset.table)){ await api("/api/tables/" + i.dataset.table + "/renommer/", {method: "POST", body: JSON.stringify({nom: v})}); n++; }
      }
      f.remove(); notifierSucces(tj("groupes.renommer_parties.ok", {v1: n}));
      if(apres) await apres();
    });
  }

  // Renommer UNE partie d'un groupe, individuellement : chaque partie a son champ et son propre bouton ; renommer l'une ne touche jamais les autres.
  async function ouvrirRenommageParties(g, apres){
    const f = ouvrirFenetre(E(g.nom), `
      ${g.tables.map(t => `<div style="display:flex;gap:6px;align-items:center;margin-bottom:6px"><input data-table="${t.id}" data-nom="${E(t.nom)}" value="${E(t.nom)}" style="flex:1;min-width:0;box-sizing:border-box"><button type="button" data-renommer="${t.id}" style="flex:none;padding:3px 10px;font-size:12px">${tj("decoupage.ui.renommer")}</button></div>`).join("")}`);
    f.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-renommer]"); if(!b) return;
      const champ = f.querySelector(`input[data-table="${b.dataset.renommer}"]`);
      const nom = champ.value.trim();
      if(!nom || nom === champ.dataset.nom) return;
      occupe(b, async () => {
        const r = await api("/api/tables/" + b.dataset.renommer + "/renommer/", {method: "POST", body: JSON.stringify({nom})});
        champ.value = champ.dataset.nom = r.table.nom;
        notifierSucces(tj("decoupage.ui.renomme_ok", {v1: r.table.nom}));
        if(apres) await apres();
      });
    });
  }

  const partieChoisie = new Map();   // id du groupe → slug de la partie choisie dans sa liste déroulante (survit aux redessins de la barre latérale)

  async function ouvrirGroupes(){
    const f = ouvrirFenetre(tj("engine/static/engine/js/index_7.js:105:30"), `
      <p style="color:var(--muted);margin:0 0 12px;font-size:12px">${tj("engine/static/engine/js/index_7.js:106:69")}</p>
      <div id="gt-contenu"></div>`);
    const zone = f.querySelector("#gt-contenu");
    const cochees = () => [...zone.querySelectorAll("input[data-table]:checked")].map(i => parseInt(i.dataset.table, 10));
    const dessiner = async () => {
      await rafraichirTables();
      const {groupes} = await api("/api/groupes-tables/");
      const libres = tables.filter(t => !t.groupe_id && !t.est_table_membres);
      const bouton = (act, id, txt, extra = "") => `<button data-act="${act}" data-id="${id}" ${extra} style="padding:3px 8px;font-size:11px">${txt}</button>`;
      zone.innerHTML = `
        <div style="border:1px solid var(--border);border-radius:8px;padding:10px;margin-bottom:12px">
          <div style="font-size:11px;color:var(--muted);text-transform:uppercase;margin-bottom:6px">${tj("engine/static/engine/js/index_7.js:117:102")}</div>
          ${libres.length ? libres.map(t => `<div style="display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap;padding:2px 0"><label style="display:flex;gap:8px;align-items:center;font-size:13px"><input type="checkbox" data-table="${t.id}" style="width:auto"> ${E(t.nom)} <span style="color:var(--muted);font-size:11px">${t.nombre_lignes} ${tj("engine/static/engine/js/index_7.js:lbl_lig")}</span></label><span style="display:flex;gap:6px">${bouton("renommer-table", t.id, tj("decoupage.ui.renommer"), `data-nom="${E(t.nom)}"`)}${t.nombre_lignes >= 2 ? bouton("decouper-table", t.id, tj("decoupage.ui.decouper"), `data-nom="${E(t.nom)}" data-nb="${t.nombre_lignes}"`) : ""}</span></div>`).join("")
            : `<div style="color:var(--muted);font-size:12px">${tj("engine/static/engine/js/index_7.js:119:64")}</div>`}
          <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap"><input id="gt-nom" placeholder="${tj("engine/static/engine/js/index_7.js:120:108")}" style="flex:1;min-width:160px"><button class="principal" data-act="creer">${tj("engine/static/engine/js/index_7.js:120:205")}</button></div>
        </div>
        ${groupes.length ? groupes.map(g => `
          <div style="border:1px solid var(--border);border-radius:8px;padding:10px;margin-bottom:10px">
            <div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:center">
              <div><strong>🔗 ${E(g.nom)}</strong> <span style="color:var(--muted);font-size:11px">${g.nb_tables} ${tj("engine/static/engine/js/index_7.js:125:115")} ${g.nb_lignes_total} lig.${g.cree_automatiquement ? tj("engine/static/engine/js/index_7.js:125:178") : ""}${g.fusionne ? " · " + tj("groupes.fusion.etat") : ""}</span></div>
              <div style="display:flex;gap:6px;flex-wrap:wrap">${g.fusionne
                ? `${bouton("ouvrir", g.id, tj("groupes.fusion.ouvrir"), `data-slug="${E(g.table_fusionnee_slug || "")}"`)}${bouton("renommer", g.id, "✎ Renommer", `data-nom="${E(g.nom)}"`)}${bouton("defusionner", g.id, tj("groupes.defusion.bouton"), `data-modifiee="${g.modifiee_depuis_fusion ? 1 : 0}"`)}`
                : `${g.nb_tables >= 2 ? bouton("fusionner", g.id, tj("groupes.fusion.bouton"), `data-nb="${g.nb_tables}" data-nom="${E(g.nom)}"`) : ""}${bouton("renommer", g.id, "✎ Renommer", `data-nom="${E(g.nom)}"`)}${bouton("rattacher", g.id, tj("engine/static/engine/js/index_7.js:126:134"))}${bouton("dissoudre", g.id, "Dissoudre")}`}${bouton("supprimer", g.id, tj("suppression.groupe.bouton"), `class="danger" data-nom="${E(g.nom)}" data-nb="${g.nb_tables}" data-lignes="${g.nb_lignes_total}"`)}</div>
            </div>
            ${g.fusionne ? `<div style="font-size:11px;color:var(--muted);margin:6px 0 2px">${tj("groupes.fusion.parties_masquees")}</div>` : ""}
            ${g.tables.map(t => g.fusionne
              ? `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:4px 0;font-size:13px;color:var(--muted)"><span>${t.partie_index || "·"}. ${E(t.nom)}</span><span style="font-size:11px;white-space:nowrap">${t.nombre_lignes} ${tj("engine/static/engine/js/index_7.js:lbl_lig")} ${bouton("renommer-table", t.id, tj("decoupage.ui.renommer"), `data-nom="${E(t.nom)}"`)}</span></div>`
              : `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:4px 0;font-size:13px">
              <a href="#" class="btn petit" data-act="ouvrir" data-slug="${E(t.slug)}">${t.partie_index || "·"}. ${E(t.nom)}</a>
              <span style="color:var(--muted);font-size:11px;white-space:nowrap">${t.nombre_lignes} ${tj("engine/static/engine/js/index_7.js:lbl_lig")} ${bouton("renommer-table", t.id, tj("decoupage.ui.renommer"), `data-nom="${E(t.nom)}"`)} ${bouton("detacher", t.id, tj("engine/static/engine/js/index_7.js:130:134"))}</span></div>`).join("")}
          </div>`).join("") : `<div style="color:var(--muted);font-size:12px">${tj("engine/static/engine/js/index_7.js:131:80")}</div>`}`;
    };
    zone.onclick = (ev) => {
      const b = ev.target.closest("[data-act]"); if(!b) return;
      ev.preventDefault();
      const id = b.dataset.id, act = b.dataset.act, base = "/api/groupes-tables/";
      if(act === "ouvrir"){ f.remove(); modeVue = "dashboard"; tableActive = b.dataset.slug; afficherTableauBord(b.dataset.slug); return; }
      if(act === "decouper-table"){ ouvrirDecoupage({id, nom: b.dataset.nom, nb: parseInt(b.dataset.nb, 10)}, dessiner); return; }
      if((act === "creer" || act === "rattacher") && !cochees().length) return notifierErreur(tj("engine/static/engine/js/index_7.js:138:96"));
      occupe(b, async () => {
        if(act === "creer") await api(base, {method: "POST", body: JSON.stringify({nom: zone.querySelector("#gt-nom").value, table_ids: cochees()})});
        if(act === "rattacher") await api(base + id + "/rattacher/", {method: "POST", body: JSON.stringify({table_ids: cochees()})});
        if(act === "detacher") await api("/api/tables/" + id + "/detacher-groupe/", {method: "POST"});
        if(act === "fusionner" && await GY.confirmer(tj("groupes.fusion.confirmer", {v1: b.dataset.nb, v2: b.dataset.nom}))){
          const r = await api(base + id + "/fusionner/", {method: "POST", body: "{}"});
          if(r.tache_id){   // grosse fusion : tâche de fond, suivie comme un import
            notifier(tj("groupes.fusion.encours", {v1: b.dataset.nom}), "info");
            for(let i = 0; i < 1800; i++){
              await new Promise(res => setTimeout(res, 2000));
              let d; try{ d = await api("/api/import/" + r.tache_id + "/statut/"); } catch(e){ continue; }
              if(!d.fini) continue;
              if(d.statut === "termine"){ notifierSucces(tj("groupes.fusion.ok_async", {v1: (d.table && d.table.nom) || b.dataset.nom})); if(d.erreur) notifier(d.erreur, "info"); }
              else notifierErreur(d.erreur || tj("groupes.fusion.echec"));
              break;
            }
          } else {
            notifierSucces(tj("groupes.fusion.ok", {v1: r.table.nom, v2: r.nb_lignes}));
            (r.avertissements || []).forEach(m => notifier(m, "info"));
          }
        }
        if(act === "defusionner"){
          const modifiee = b.dataset.modifiee === "1";
          if(await GY.confirmer(tj(modifiee ? "groupes.defusion.modifiee" : "groupes.defusion.confirmer")))
            await api(base + id + "/defusionner/", {method: "POST", body: JSON.stringify({confirmer: modifiee})});
        }
        if(act === "dissoudre" && await GY.confirmer(tj("engine/static/engine/js/index_7.js:143:44"))) await api(base + id + "/", {method: "DELETE"});
        if(act === "supprimer" && await GY.confirmer(tj("suppression.groupe.confirmer", {v1: b.dataset.nom, v2: b.dataset.nb, v3: b.dataset.lignes})))
          await window.gySupprimerEnMasse(base + id + "/?donnees=1", {method: "DELETE"}, b.dataset.nom);
        if(act === "renommer-table"){
          const nom = await GY.demander(tj("decoupage.ui.prompt_renommer"), b.dataset.nom);
          if(nom && nom.trim() && nom.trim() !== b.dataset.nom){
            const r = await api("/api/tables/" + id + "/renommer/", {method: "POST", body: JSON.stringify({nom: nom.trim()})});
            notifierSucces(tj("decoupage.ui.renomme_ok", {v1: r.table.nom}));
          }
        }
        if(act === "renommer"){
          const g = (await api(base)).groupes.find(x => String(x.id) === String(id));
          if(g) await ouvrirRenommageGroupe(g, dessiner);
        }
        await dessiner();
      });
    };
    await dessiner();
  }

  // ── Conteneurs : tables et groupes hétérogènes rangés ensemble (un groupe = une table) ──
  async function ouvrirConteneurs(){
    const f = ouvrirFenetre(tj("engine/static/engine/js/index_7.js:lbl_conteneurs"), `
      <p style="color:var(--muted);margin:0 0 12px;font-size:12px">${tj("engine/static/engine/js/index_7.js:154:69")}</p>
      <div id="ct-contenu"></div>`);
    const zone = f.querySelector("#ct-contenu");
    const cochees = (attr) => [...zone.querySelectorAll(`input[data-${attr}]:checked`)].map(i => parseInt(i.dataset[attr], 10));
    const bouton = (act, id, txt, extra = "") => `<button data-act="${act}" data-id="${id}" ${extra} style="padding:3px 8px;font-size:11px">${txt}</button>`;
    const dessiner = async () => {
      await rafraichirTables();
      const [{conteneurs}, {groupes}] = await Promise.all([api("/api/conteneurs-tables/"), api("/api/groupes-tables/")]);
      const rT = new Set(conteneurs.flatMap(c => c.tables.map(t => t.id))), rG = new Set(conteneurs.flatMap(c => c.groupes.map(g => g.id)));
      const libres = tables.filter(t => !t.groupe_id && !t.est_table_membres && !rT.has(t.id));  // un membre ne se range que dans UN conteneur
      const choix = [...groupes.filter(g => !rG.has(g.id)).map(g => `<label style="display:flex;gap:8px;align-items:center;font-size:13px;padding:2px 0"><input type="checkbox" data-groupe="${g.id}" style="width:auto"> ${tj("engine/static/engine/js/index_7.js:191:219", {v1: E(g.nom)})} <span style="color:var(--muted);font-size:11px">${g.nb_tables} ${tj("engine/static/engine/js/index_7.js:164:304")}</span></label>`),
        ...libres.map(t => `<label style="display:flex;gap:8px;align-items:center;font-size:13px;padding:2px 0"><input type="checkbox" data-table="${t.id}" style="width:auto"> ${E(t.nom)} <span style="color:var(--muted);font-size:11px">${tj("engine/static/engine/js/index_7.js:192:237", {v1: t.nombre_lignes})}</span></label>`)];
      zone.innerHTML = `
        <div style="border:1px solid var(--border);border-radius:8px;padding:10px;margin-bottom:12px">
          <div style="font-size:11px;color:var(--muted);text-transform:uppercase;margin-bottom:6px">${tj("engine/static/engine/js/index_7.js:168:102")}</div>
          ${choix.length ? choix.join("") : `<div style="color:var(--muted);font-size:12px">${tj("engine/static/engine/js/index_7.js:169:94")}</div>`}
          <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap"><input id="ct-nom" placeholder="${tj("engine/static/engine/js/index_7.js:170:108")}" style="flex:1;min-width:160px"><button class="principal" data-act="creer">${tj("engine/static/engine/js/index_7.js:170:208")}</button></div>
        </div>
        ${conteneurs.length ? conteneurs.map(c => `
          <div style="border:1px solid var(--border);border-radius:8px;padding:10px;margin-bottom:10px">
            <div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:center">
              <strong>${E(c.nom)} <span style="color:var(--muted);font-size:11px;font-weight:400">${c.tables.length + c.groupes.length} membre(s)</span></strong>
              <div style="display:flex;gap:6px;flex-wrap:wrap">${bouton("renommer", c.id, tj("engine/static/engine/js/index_7.js:lbl_renommer"))}${bouton("ajouter", c.id, tj("engine/static/engine/js/index_7.js:176:130"))}${bouton("supprimer", c.id, tj("engine/static/engine/js/index_7.js:176:182"))}</div>
            </div>
            ${c.groupes.map(g => `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:4px 0;font-size:13px"><a href="#" class="btn petit" data-act="ouvrir" data-slug="${E(g.slug)}">${tj("engine/static/engine/js/index_7.js:lbl_groupe", {v1: E(g.nom)})}</a><span style="color:var(--muted);font-size:11px">${g.nb_tables} ${tj("engine/static/engine/js/index_7.js:178:318")} ${bouton("retirer", c.id, "Retirer", `data-groupe="${g.id}"`)}</span></div>`).join("")}
            ${c.tables.map(t => `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:4px 0;font-size:13px"><a href="#" class="btn petit" data-act="ouvrir" data-slug="${E(t.slug)}">${E(t.nom)}</a><span>${bouton("retirer", c.id, "Retirer", tj("engine/static/engine/js/index_7.js:179:288", {v1: t.id}))}</span></div>`).join("")}
          </div>`).join("") : `<div style="color:var(--muted);font-size:12px">${tj("engine/static/engine/js/index_7.js:180:80")}</div>`}`;
      zone._conteneurs = conteneurs;
    };
    zone.onclick = (ev) => {
      const b = ev.target.closest("[data-act]"); if(!b) return;
      ev.preventDefault();
      const act = b.dataset.act, id = b.dataset.id, base = "/api/conteneurs-tables/";
      if(act === "ouvrir"){ f.remove(); modeVue = "dashboard"; tableActive = b.dataset.slug; afficherTableauBord(b.dataset.slug); return; }
      const c = (zone._conteneurs || []).find(x => String(x.id) === id);
      const ids = c ? {t: c.tables.map(x => x.id), g: c.groupes.map(x => x.id)} : {t: [], g: []};
      if((act === "creer" || act === "ajouter") && !cochees("table").length && !cochees("groupe").length) return notifierErreur(tj("engine/static/engine/js/index_7.js:190:130"));
      occupe(b, async () => {
        const envoyer = (url, methode, corps) => api(url, {method: methode, body: JSON.stringify(corps)});
        if(act === "creer") await envoyer(base, "POST", {nom: zone.querySelector("#ct-nom").value, table_ids: cochees("table"), groupe_ids: cochees("groupe")});
        if(act === "ajouter") await envoyer(base + id + "/", "PATCH", {table_ids: [...new Set([...ids.t, ...cochees("table")])], groupe_ids: [...new Set([...ids.g, ...cochees("groupe")])]});
        if(act === "retirer") await envoyer(base + id + "/", "PATCH", {table_ids: ids.t.filter(x => String(x) !== b.dataset.table), groupe_ids: ids.g.filter(x => String(x) !== b.dataset.groupe)});
        if(act === "supprimer" && await GY.confirmer(tj("engine/static/engine/js/index_7.js:196:44"))) await api(base + id + "/", {method: "DELETE"});
        if(act === "renommer"){ const nom = await GY.demander(tj("engine/static/engine/js/index_7.js:197:53")); if(nom && nom.trim()) await envoyer(base + id + "/", "PATCH", {nom: nom.trim()}); }
        await dessiner();
      });
    };
    await dessiner();
  }

  // ── Barre latérale : un groupe se présente comme UNE table (sa première partie porte l'analyse de tout
  // le groupe), et chaque conteneur liste ses membres (tables et groupes). Appelé par rafraichirTables. ──
  window.dessinerGroupesConteneurs = async (liste, deja) => {
    let groupes = [], conteneurs = [];
    try { [{groupes}, conteneurs] = await Promise.all([api("/api/groupes-tables/"), deja || api("/api/conteneurs-tables/").then(r => r.conteneurs)]); } catch(e) { return; }
    const rG = new Set(conteneurs.flatMap(c => c.groupes.map(g => g.id)));  // groupe rangé : visible seulement dans son conteneur
    const entree = (txt, detail, slug, marge = 0, actifSauf = null, sortir = null, detailPopup = null, renom = null) => {
      const div = document.createElement("div");
      div.className = "item-table" + ((modeVue === "dashboard" || modeVue === "table") && tableActive === slug && slug !== actifSauf ? " actif" : "");
      div.style.marginLeft = marge + "px";
      div.innerHTML = `${txt} <span class="n">${detail}</span>`;
      if(sortir){   // ⤴ : sortir l'élément de son groupe / de son conteneur (il redevient autonome ou reste dans le conteneur)
        const b = document.createElement("button");
        b.textContent = "⤴"; b.title = sortir.titre; b.style.cssText = "float:right;padding:0 6px;font-size:11px;margin-left:6px";
        b.onclick = async (ev) => { ev.stopPropagation(); try { await sortir.fn(); notifierSucces(sortir.ok); await rafraichirTables(); } catch(e) { /* api() a déjà signalé l'erreur */ } };
        div.prepend(b);
      }
      if(renom){   // ✎ : renommer (un groupe : ses tables enfants suivent, le serveur les renomme aussi)
        const r = document.createElement("button");
        r.type = "button"; r.textContent = "✎"; r.title = renom.titre; r.style.cssText = "float:right;padding:0 6px;font-size:11px;margin-left:6px";
        r.onclick = async (ev) => { ev.stopPropagation(); try { await renom.fn(); } catch(e) { /* api() a déjà signalé l'erreur */ } };
        div.prepend(r);
      }
      if(window.GYApparence && slug){ window.GYApparence.appliquerTable(div, slug); div.prepend(window.GYApparence.boutonTable(slug, div)); }   // Aa : fond + texte de l'entrée
      if(window.gyBoutonColonnesTable && slug) div.prepend(window.gyBoutonColonnesTable(slug));   // ▦ colonnes + types, après le ⓘ
      if(window.gyBoutonInfoTable) div.prepend(window.gyBoutonInfoTable(txt, detailPopup || detail));   // ⓘ à gauche (toujours en tête, avant ⤴)
      div.onclick = () => { modeVue = "table"; tableActive = slug; afficherTable(slug); rafraichirTables(); if(window.innerWidth <= 768) fermerSidebar(); };
      liste.appendChild(div);
    };
    // Détail complet d'une table (lignes + colonnes) pour le popup ⓘ : lu dans la liste des tables déjà chargée.
    const detailTable = (slug, repli) => { const x = (typeof tables !== "undefined" ? tables : []).find(y => y.slug === slug); return x ? tj("engine/static/engine/js/index_1.js:527:67", {v1: x.nombre_lignes, v2: (x.champs || []).length, v3: ""}) : repli; };
    const ancre = (g) => g.tables.length ? g.tables[0].slug : "";  // 1re partie = ancre stable (dashboards, KPI, analyses du groupe)
    const detailGroupe = (g) => { const x = (typeof tables !== "undefined" ? tables : []).find(y => y.slug === ancre(g)); return tj("engine/static/engine/js/index_7.js:lignes_parties", {v1: g.nb_lignes_total, v2: g.nb_tables}) + (x ? " · " + tj("engine/static/engine/js/index_1.js:colonnes_table_entete", {v1: (x.champs || []).length}) : ""); };  // popup ⓘ du groupe : + colonnes
    const parId = new Map(groupes.map(g => [g.id, g]));
    // Renommage depuis la barre latérale : le groupe (ses parties et sa table fusionnée suivent) ou une table seule.
    const renommerGroupe = (g) => ({titre: tj("decoupage.ui.renommer"), fn: async () => {
      const nom = await GY.demander(tj("engine/static/engine/js/index_7.js:144:53"), g.nom);
      if(!nom || !nom.trim() || nom.trim() === g.nom) return;
      await api("/api/groupes-tables/" + g.id + "/", {method: "PATCH", body: JSON.stringify({nom: nom.trim()})});   // le groupe seul ; ses parties suivent côté serveur seulement si leur nom commence par le sien
      await rafraichirTables();
    }});
    const renommerTable = (t) => ({titre: tj("decoupage.ui.renommer"), fn: async () => {
      const nom = await GY.demander(tj("decoupage.ui.prompt_renommer"), t.nom);
      if(!nom || !nom.trim() || nom.trim() === t.nom) return;
      const r = await api("/api/tables/" + t.id + "/renommer/", {method: "POST", body: JSON.stringify({nom: nom.trim()})});
      notifierSucces(tj("decoupage.ui.renomme_ok", {v1: r.table.nom}));
      await rafraichirTables();
    }});
    // Un groupe = une entrée (analyse de tout le groupe) + ses parties dessous : c'est le SEUL endroit où elles se voient.
    const dessinerGroupe = (g, marge, c = null) => {
      entree(tj("engine/static/engine/js/index_7.js:groupe_lien", {v1: E(g.nom)}), tj("engine/static/engine/js/index_7.js:lignes_parties", {v1: g.nb_lignes_total, v2: g.nb_tables}), ancre(g), marge, null,
        c && {titre: tj("engine/static/engine/js/index_7.js:229:23"), ok: tj("engine/static/engine/js/index_7.js:229:60"), fn: () => api("/api/conteneurs-tables/" + c.id + "/", {method: "PATCH", body: JSON.stringify({groupe_ids: c.groupes.map(x => x.id).filter(x => x !== g.id)})})}, detailGroupe(g), renommerGroupe(g));
      // Les parties ne s'étalent plus dans la barre latérale : une liste déroulante (combo box) par groupe, on choisit la partie à ouvrir.
      const ligne = document.createElement("div");
      ligne.style.cssText = `display:flex;gap:6px;align-items:center;margin:2px 10px 6px ${marge + 14}px`;
      const sel = document.createElement("select");
      sel.style.cssText = "flex:1;min-width:0;padding:4px 6px;font-size:12px;background:var(--panel,#0a0a0a);color:var(--text);border:1px solid var(--border);border-radius:4px";
      sel.title = tj("engine/static/engine/js/index_7.js:groupe_parties_combo", {v1: g.tables.length});
      const vide = document.createElement("option");
      vide.value = ""; vide.textContent = tj("engine/static/engine/js/index_7.js:groupe_parties_combo", {v1: g.tables.length});
      sel.appendChild(vide);
      for(const t of g.tables){
        const o = document.createElement("option");
        o.value = t.slug; o.textContent = `${t.nom} · ${t.nombre_lignes} ${tj("engine/static/engine/js/index_7.js:lbl_lig")}`;
        sel.appendChild(o);
      }
      // Partie actuellement ouverte (autre que l'ancre, qui est le groupe lui-même) : présélectionnée après chaque rafraîchissement
      // La partie choisie est mémorisée PAR GROUPE (partieChoisie) : la 1re partie sert d'ancre au groupe (même slug), donc « tableActive ≠ ancre »
      // l'excluait et la liste revenait sur « choisir… » après chaque rafraîchissement — seules les autres parties restaient sélectionnées.
      const choisie = partieChoisie.get(g.id);
      const ouverte = (modeVue === "dashboard" || modeVue === "table") && choisie && choisie === tableActive && g.tables.some(t => t.slug === choisie) ? choisie : "";
      sel.value = ouverte;
      const titreSel = () => { const o = sel.selectedOptions[0]; if(o && sel.value) sel.title = o.textContent; };   // nom complet même coupé par la largeur
      titreSel();
      // ⤴ : détacher la partie choisie du groupe (elle redevient une table autonome)
      const sortir = document.createElement("button");
      sortir.textContent = "⤴"; sortir.title = tj("engine/static/engine/js/index_7.js:231:18");
      sortir.style.cssText = "padding:0 6px;font-size:11px;flex:none";
      sortir.style.display = sel.value ? "" : "none";
      sortir.onclick = async (ev) => {
        ev.stopPropagation();
        const t = g.tables.find(x => x.slug === sel.value); if(!t) return;
        try { await api("/api/tables/" + t.id + "/detacher-groupe/", {method: "POST"}); notifierSucces(tj("engine/static/engine/js/index_7.js:231:54")); await rafraichirTables(); } catch(e) { /* api() a déjà signalé l'erreur */ }
      };
      sel.onchange = () => {
        sortir.style.display = sel.value ? "" : "none";
        titreSel();
        if(sel.value) partieChoisie.set(g.id, sel.value); else partieChoisie.delete(g.id);
        if(!sel.value) return;
        modeVue = "table"; tableActive = sel.value; afficherTable(sel.value); rafraichirTables();
        if(window.innerWidth <= 768) fermerSidebar();
      };
      const renomPartie = document.createElement("button");   // ✎ : renommer la partie choisie
      renomPartie.type = "button"; renomPartie.textContent = "✎"; renomPartie.title = tj("decoupage.ui.renommer");
      renomPartie.style.cssText = "padding:0 6px;font-size:11px;flex:none";
      renomPartie.onclick = (ev) => { ev.stopPropagation(); ouvrirRenommageParties(g, rafraichirTables); };   // chaque partie, individuellement
      ligne.appendChild(sel); ligne.appendChild(renomPartie); ligne.appendChild(sortir);
      liste.appendChild(ligne);
    };
    for(const g of groupes.filter(g => g.tables.length && !rG.has(g.id))) dessinerGroupe(g, 0);
    for(const c of conteneurs){
      const titre = document.createElement("div");
      titre.style.cssText = "padding:8px 10px 2px;font-size:11px;color:var(--muted);text-transform:uppercase";
      titre.textContent = "Conteneur : " + c.nom;
      liste.appendChild(titre);
      for(const gc of c.groupes){ const g = parId.get(gc.id); if(g && g.tables.length) dessinerGroupe(g, 10, c); }
      for(const t of c.tables) entree(E(t.nom), "", t.slug, 10, null,
        {titre: tj("engine/static/engine/js/index_7.js:241:18"), ok: tj("engine/static/engine/js/index_7.js:241:57"), fn: () => api("/api/conteneurs-tables/" + c.id + "/", {method: "PATCH", body: JSON.stringify({table_ids: c.tables.map(x => x.id).filter(x => x !== t.id)})})}, detailTable(t.slug, null), renommerTable(t));
    }
  };

  // ── Clôture de cycle ─────────────────────────────────────────────────────
  async function ouvrirCloture(){
    const f = ouvrirFenetre(tj("engine/static/engine/js/index_7.js:247:30"), `
      <p style="color:var(--muted);margin:0 0 12px;font-size:12px">${tj("engine/static/engine/js/index_7.js:248:69")}</p>
      <button class="principal" id="cy-cloturer" style="border:1px solid var(--danger);color:var(--danger)">${tj("engine/static/engine/js/index_7.js:249:110")}</button>
      <div style="border-top:1px solid var(--border);margin:14px 0 10px;padding-top:10px">
        <div style="font-size:11px;color:var(--muted);text-transform:uppercase;margin-bottom:6px">${tj("engine/static/engine/js/index_7.js:251:100")}</div>
        <p style="color:var(--muted);margin:0 0 8px;font-size:12px">${tj("engine/static/engine/js/index_7.js:252:70")}</p>
        <a href="/api/entreprise/modele/exporter/" download id="cy-modele" style="display:inline-block;border:1px solid var(--border);border-radius:6px;padding:5px 10px;font-size:13px;text-decoration:none;color:inherit">${tj("engine/static/engine/js/index_7.js:253:222")}</a>
      </div>
      <div style="border-top:1px solid var(--border);margin:14px 0 10px;padding-top:10px">
        <div style="font-size:11px;color:var(--muted);text-transform:uppercase;margin-bottom:6px">${tj("engine/static/engine/js/index_7.js:283:99")}</div>
        <label style="font-size:12px;display:flex;gap:6px;align-items:center"><input type="checkbox" id="cy-auto"> ${tj("engine/static/engine/js/index_7.js:257:117")} <span id="cy-auto-n">10</span> ${tj("engine/static/engine/js/index_7.js:257:219")}</label>
        <div id="cy-images"></div>
      </div>
      <div style="border-top:1px solid var(--border);margin:14px 0 10px;padding-top:10px">
        <div style="font-size:11px;color:var(--muted);text-transform:uppercase;margin-bottom:6px">${tj("engine/static/engine/js/index_7.js:288:99")}</div>
        <div id="cy-archives"></div>
        <div style="display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap">
          <input type="file" id="cy-fichier" accept=".zip" style="flex:1;min-width:160px">
          <button id="cy-restaurer-fichier">${tj("engine/static/engine/js/index_7.js:265:46")}</button>
        </div>
      </div>`);
    const $ = (id) => f.querySelector("#" + id);
    const dessinerArchives = async () => {
      const {archives} = await api("/api/entreprise/archives-cycle/");
      $("cy-archives").innerHTML = archives.length ? archives.map(a =>
        `<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;font-size:13px">
          <span>${E(a.cree_le.slice(0, 16).replace("T", " "))}</span>
          <button data-id="${a.id}" style="padding:3px 8px;font-size:11px">${tj("engine/static/engine/js/index_7.js:301:76")}</button>
        </div>`).join("") : `<div style="color:var(--muted);font-size:12px">${tj("engine/static/engine/js/index_7.js:275:78")}</div>`;
    };
    const dessinerImages = async () => {
      const r = await api("/api/entreprise/images-auto/");
      $("cy-auto").checked = !!r.actif; $("cy-auto-n").textContent = r.garder;
      $("cy-images").innerHTML = r.images.map(a =>
        `<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;font-size:13px">
          <span>${E(a.cree_le.slice(0, 19).replace("T", " "))} · ${Math.max(1, Math.round(a.taille / 1024))} ${tj("engine/static/engine/js/index_7.js:lbl_unite_ko")}</span>
          <button data-nom="${E(a.nom)}" style="padding:3px 8px;font-size:11px">${tj("engine/static/engine/js/index_7.js:310:81")}</button>
        </div>`).join("");
    };
    $("cy-auto").onchange = (ev) => occupe(ev.currentTarget, async () => {
      await api("/api/entreprise/images-auto/", {method: "POST", body: JSON.stringify({actif: ev.currentTarget.checked})});
      await dessinerImages();
    });
    $("cy-images").onclick = (ev) => {
      const b = ev.target.closest("button[data-nom]"); if(!b) return;
      occupe(b, async () => {
        if(!await GY.confirmer(tj("engine/static/engine/js/index_7.js:293:22"))) return;
        await api("/api/entreprise/images-auto/restaurer/", {method: "POST", body: JSON.stringify({nom: b.dataset.nom})});
        notifierSucces(tj("engine/static/engine/js/index_7.js:295:25")); f.remove(); await rafraichirTables();
      });
    };
    $("cy-cloturer").onclick = (ev) => occupe(ev.currentTarget, async () => {
      if(!await GY.confirmer(tj("engine/static/engine/js/index_7.js:299:20"))) return;
      await api("/api/entreprise/cloturer/", {method: "POST"});
      notifierSucces(tj("engine/static/engine/js/index_7.js:301:23"));
      await dessinerArchives(); await rafraichirTables();
    });
    $("cy-archives").onclick = (ev) => {
      const b = ev.target.closest("button[data-id]"); if(!b) return;
      occupe(b, async () => {
        if(!await GY.confirmer(tj("engine/static/engine/js/index_7.js:307:22"))) return;
        await api("/api/entreprise/restaurer/", {method: "POST", body: JSON.stringify({archive_id: b.dataset.id})});
        notifierSucces(tj("engine/static/engine/js/index_7.js:309:25")); f.remove(); await rafraichirTables();
      });
    };
    $("cy-restaurer-fichier").onclick = (ev) => occupe(ev.currentTarget, async () => {
      const fichier = $("cy-fichier").files[0];
      if(!fichier) return notifierErreur(tj("engine/static/engine/js/index_7.js:314:43"));
      if(!await GY.confirmer(tj("engine/static/engine/js/index_7.js:315:20"))) return;
      const fd = new FormData(); fd.append("fichier", fichier);
      await api("/api/entreprise/restaurer/", {method: "POST", body: fd});
      notifierSucces(tj("engine/static/engine/js/index_7.js:318:23")); f.remove(); await rafraichirTables();
    });
    await dessinerArchives(); await dessinerImages();
  }

  const btnCe = document.getElementById("btn-connexion-externe"), btnGt = document.getElementById("btn-groupes-tables"), btnCy = document.getElementById("btn-cloture-cycle");
  if(btnCe) btnCe.onclick = () => ouvrirConnexions().catch(() => {});
  if(btnGt) btnGt.onclick = () => ouvrirGroupes().catch(() => {});
  const btnCt = document.getElementById("btn-conteneurs-tables");
  if(btnCt) btnCt.onclick = () => ouvrirConteneurs().catch(() => {});
  if(btnCy) btnCy.onclick = () => ouvrirCloture().catch(() => {});
})();
