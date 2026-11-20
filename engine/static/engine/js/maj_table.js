// Bouton « Mettre à jour » de la barre d'une table : importe CSV / Excel / Parquet / JSON et AJOUTE les lignes (rien n'est écrasé).
// Fichier séparé : index_1.js reste intact ; le bouton s'insère dès que la barre de la table est affichée. Route : /api/tables/<id>/lignes/importer/
(function () {
  const K = "engine/static/engine/js/maj_table.js:";
  const T = (cle, repli) => { try { const v = typeof tj === "function" ? tj(K + cle) : ""; return v && v !== K + cle ? v : repli; } catch (e) { return repli; } };
  const dire = (m, erreur) => (erreur ? (typeof notifierErreur === "function" ? notifierErreur(m) : alert(m)) : (typeof notifierSucces === "function" ? notifierSucces(m) : alert(m)));

  async function envoyer(table, fichier, bouton, fd = new FormData()) {
    if (fichier) fd.append("fichier", fichier);
    bouton.disabled = true;
    try {
      let res;
      if (typeof api === "function") res = await api(`/api/tables/${table.id}/lignes/importer/`, { method: "POST", body: fd });   // avec le helper de la page (CSRF, erreurs)
      else {                                                                                                                       // sans : appel direct
        const m = document.cookie.match(/(?:^|; )gy_csrftoken=([^;]*)/);
        const r = await fetch(`/api/tables/${table.id}/lignes/importer/`, { method: "POST", body: fd, headers: { "X-CSRFToken": m ? decodeURIComponent(m[1]) : "" } });
        res = await r.json().catch(() => ({})); if (!r.ok) throw new Error(res.erreur || r.statusText);
      }
      dire((res && res.message) || T("ok", tj("engine/static/engine/js/maj_table.js:19:45")));
      if (typeof afficherTable === "function" && typeof tableActive !== "undefined" && tableActive) await afficherTable(tableActive);
    } catch (e) { if (typeof api !== "function") dire(e.message || T("echec", tj("engine/static/engine/js/maj_table.js:21:80")), true); return e; }   // avec api() l'erreur est déjà affichée ; l'erreur est rendue à l'appelant (code « jeton_a_reconfigurer »)
    finally { bouton.disabled = false; }
  }

  // 🔑 Online Sheet : le jeton peut être régénéré à tout moment côté application distante → on le ressaisit ici ; l'adresse mémorisée est conservée et le fichier est relu aussitôt.
  const marquerJeton = (b, table) => { b.style.background = table.jeton_a_reconfigurer ? "#e67e22" : ""; b.title = T(table.jeton_a_reconfigurer ? "jeton_a_reconfigurer" : "jeton_titre", tj(K + (table.jeton_a_reconfigurer ? "jeton_a_reconfigurer" : "jeton_titre"))); };
  async function reconfigurerJeton(table) {
    const b = document.getElementById("btn-jeton-source"); if (!b) return;
    const j = await GY.demander(T("jeton_nouveau", tj(K + "jeton_nouveau")), "", { masque: true }); if (!j || !j.trim()) return;
    const fd = new FormData(); fd.append("jeton", j.trim());
    const e = await envoyer(table, null, b, fd);
    table.jeton_a_reconfigurer = !!(e && e.code === "jeton_a_reconfigurer"); if (!e) table.source_securisee = true; marquerJeton(b, table);
  }

  function installer() {
    const ref = document.getElementById("btn-exporter-table");
    if (!ref || document.getElementById("btn-maj-table") || typeof tables === "undefined" || typeof tableActive === "undefined") return;
    const table = tables.find((x) => x.slug === tableActive); if (!table) return;
    const b = document.createElement("button");
    b.id = "btn-maj-table"; b.style.cssText = "font-size:12px;padding:4px 8px"; b.title = T("aide", tj("engine/static/engine/js/maj_table.js:30:102"));
    b.textContent = "⬆ " + T("bouton", tj("engine/static/engine/js/maj_table.js:31:41"));
    const f = document.createElement("input"); f.type = "file"; f.accept = ".csv,.xlsx,.xls,.parquet,.json"; f.style.display = "none";
    f.onchange = () => { if (f.files[0]) envoyer(table, f.files[0], b).then(() => { f.value = ""; }); };
    b.onclick = () => f.click();
    ref.parentNode.insertBefore(b, ref); ref.parentNode.insertBefore(f, ref);
    if (!table.url_source) return;   // table née d'un Google Sheet ou d'un fichier en ligne : ↻ relit le lien, ⏱ règle l'intervalle de relecture automatique
    const r = Object.assign(document.createElement("button"), { id: "btn-maj-gsheet", textContent: "↻", title: /^\s*https?:\/\/docs\.google\.com\/spreadsheets/i.test(table.url_source) ? T("recharger", tj("engine/static/engine/js/maj_table.js:37:203")) : T("recharger_en_ligne", tj("engine/static/engine/js/maj_table.js:37:275")), style: b.style.cssText }), h = Object.assign(document.createElement("button"), { id: "btn-intervalle-gsheet", textContent: "⏱", title: T("intervalle_titre", tj("engine/static/engine/js/maj_table.js:37:483")), style: b.style.cssText });
    r.onclick = async () => { const e = await envoyer(table, null, r); if (e && e.code === "jeton_a_reconfigurer") { table.jeton_a_reconfigurer = true; const k = document.getElementById("btn-jeton-source"); if (k) marquerJeton(k, table); reconfigurerJeton(table); } };   // jeton refusé : proposition immédiate d'en saisir un nouveau
    h.onclick = async () => { const m = await GY.demander(T("intervalle_demande", tj("engine/static/engine/js/maj_table.js:39:84")), String(table.intervalle_source)); if (m === null) return; const fd = new FormData(); fd.append("intervalle", m); table.intervalle_source = +m || 0; envoyer(table, null, h, fd); };
    ref.parentNode.insertBefore(r, ref); ref.parentNode.insertBefore(h, ref);
    if (/^\s*https?:\/\/docs\.google\.com\/spreadsheets/i.test(table.url_source)) return;   // le jeton ne concerne que les fichiers en ligne (Online Sheet), pas Google Sheet
    const k = Object.assign(document.createElement("button"), { id: "btn-jeton-source", textContent: "🔑", style: b.style.cssText });
    k.onclick = () => reconfigurerJeton(table); marquerJeton(k, table); ref.parentNode.insertBefore(k, ref);
    if (table.jeton_a_reconfigurer) dire(T("jeton_a_reconfigurer", tj(K + "jeton_a_reconfigurer")), true);   // à l'ouverture de la table : rappel que le jeton est à reconfigurer
  }

  // « Google Sheet » et « Online Sheet » : créent la table depuis un lien (même import asynchrone, découpé en groupe si trop gros ;
  // le lien est mémorisé : ↻ et la relecture automatique fonctionnent ensuite de la même façon pour les deux).
  function boutonCreation(ref, apres, id, icone, cle, defaut, cleUrl, defautUrl, cleAide, securise) {
    const deja = document.getElementById(id); if (deja) return deja;
    const b = Object.assign(document.createElement("button"), { id, textContent: icone + " " + T(cle, defaut), style: ref.style.cssText });
    const aide = T(cleAide, ""); if (aide) b.dataset.guide = aide;   // texte 100 % issu du catalogue i18n (fr.json / en.json), aucun littéral en dur   // infobule de la barre latérale (même mécanisme data-guide que les autres boutons)
    b.onclick = async () => { const u = await GY.demander(T(cleUrl, defautUrl), ""); if (!u) return; const fd = new FormData(); fd.append("url", u); if (securise) { const j = await GY.demander(T("online_secure_jeton", tj(K + "online_secure_jeton")), "", { masque: true }); if (!j) return; fd.append("jeton", j.trim()); } try { const r = await api("/api/tables/importer-fichier/", { method: "POST", body: fd }); await _pollerImport(r.tache_id, r.nom_fichier); } catch (e) { /* api() affiche déjà l'erreur */ } };
    ref.parentNode.insertBefore(b, apres.nextSibling);
    return b;
  }

  function installerCreation() {
    const ref = document.getElementById("btn-importer-fichier"); if (!ref || typeof _pollerImport !== "function") return;
    const g = boutonCreation(ref, ref, "btn-gsheet", "🔗", "gsheet", "Google Sheet", "gsheet_url", tj("engine/static/engine/js/maj_table.js:55:100"), "gsheet_aide");
    const o = boutonCreation(ref, g, "btn-online-sheet", "🌐", "online_sheet", "Online Sheet", "online_url", tj("engine/static/engine/js/maj_table.js:56:100"), "online_aide");
    boutonCreation(ref, o, "btn-online-sheet-securise", "🔐", "online_sheet_secure", tj(K + "online_sheet_secure"), "online_secure_url", tj(K + "online_secure_url"), "online_secure_aide", true);   // adresse + jeton d'accès (HTTPS seulement)
  }

  const tout = () => { installer(); installerCreation(); };
  new MutationObserver(tout).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("DOMContentLoaded", tout);
})();
