/* SÉLECTEUR DE TABLE DES PAGES — une liste déroulante en tête de chaque page liée à une table (/tables/<id>/<page>/).
 *
 * Elle propose TOUTES les tables que la personne peut utiliser : les tables de données ordinaires, puis les tables système (membres,
 * wallet, paiement, messagerie… — voir engine/tables_systeme.py). Choisir une table ouvre la MÊME page sur cette table.
 * Les droits sont ceux de /api/tables/?systeme=1 : un client ne voit que les tables exposées, un agent les tables système que
 * l'administrateur lui a autorisées, l'administrateur toutes.
 *
 * Aucun gabarit à écrire : ajouter <script src=".../selecteur_table_page.js" defer></script> à la page suffit. Le sélecteur se place sous la
 * marque de la barre latérale (#sidebar #marque) ou, à défaut, dans l'en-tête (.header).
 */
(function () {
  "use strict";
  var m = location.pathname.match(/^(\/tables\/)(\d+)(\/[^?#]*)$/);
  if (!m) return;
  if (window.LECTURE_SEULE_PARTAGE) return;   // consultation seule (client / partage) : la table est figée, pas de changement de table
  var courant = parseInt(m[2], 10), suffixe = m[3];
  var K = "engine/static/engine/js/selecteur_table_page.js:";   // clés du catalogue i18n (tj défini par {% i18n_js %})
  function T(cle) { return window.tj ? window.tj(K + cle) : cle; }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }

  function options(liste, actuelle) {
    return liste.map(function (t) {
      return '<option value="' + t.id + '"' + (t.id === actuelle ? " selected" : "") + ">" + esc(t.nom) + "</option>";
    }).join("");
  }

  function construire(tables) {
    var ordinaires = tables.filter(function (t) { return !t.systeme && !t.source_systeme; });
    var systeme = tables.filter(function (t) { return t.systeme || t.source_systeme; });
    var tri = function (a, b) { return String(a.nom).localeCompare(String(b.nom), "fr", {sensitivity: "base"}); };
    ordinaires.sort(tri); systeme.sort(tri);
    var presente = tables.some(function (t) { return t.id === courant; });
    var html = presente ? "" : '<option value="' + courant + '" selected>' + esc(document.title.split(" — ").pop() || ("#" + courant)) + "</option>";
    if (ordinaires.length) html += '<optgroup label="' + esc(T("38:44")) + '">' + options(ordinaires, courant) + "</optgroup>";
    if (systeme.length) html += '<optgroup label="' + esc(T("39:44")) + '">' + options(systeme, courant) + "</optgroup>";

    var bloc = document.createElement("div");
    bloc.id = "sel-table-page-bloc";
    bloc.style.cssText = "padding:6px 10px 8px";
    bloc.innerHTML = '<label for="sel-table-page" style="display:block;font-size:11px;color:var(--muted);margin-bottom:3px">' + esc(T("44:120")) + "</label>" +
      tj("engine/static/engine/js/selecteur_table_page.js:46:8") + esc(T("45:78")) + '">' + html + "</select>";
    bloc.querySelector("select").addEventListener("change", function (ev) {
      var id = parseInt(ev.target.value, 10);
      if (id && id !== courant) location.href = m[1] + id + suffixe;
    });
    return bloc;
  }

  function placer(bloc) {
    var marque = document.querySelector("#sidebar #marque");
    if (marque && marque.parentNode) { marque.parentNode.insertBefore(bloc, marque.nextSibling); return; }
    // marque.js remonte #marque dans le bandeau du haut (bandeau fusionné) : le sélecteur prend alors la tête de la barre latérale.
    var barre = document.getElementById("sidebar");
    if (barre) { barre.insertBefore(bloc, barre.firstChild); return; }
    var entete = document.querySelector(".header");
    if (entete) {
      // Ordre du bandeau (voir marque.js) : ☰ · Modules · logo + nom · titre · … Le sélecteur ne s'intercale JAMAIS entre l'icône Modules et le
      // logo (ils vont toujours côte à côte, quel que soit celui des deux scripts qui finit en premier) : il se place après le titre de la page, avant le bouton retour (l'ordre est de toute façon imposé par le CSS : voir `order` sur .header).
      // Largeur souple (pas de min-width rigide) : sur téléphone le sélecteur rétrécit au lieu de passer à la ligne et de chasser le titre.
      bloc.style.padding = "0 10px"; bloc.style.flex = "0 1 240px"; bloc.style.minWidth = "130px";
      var titre = entete.querySelector(":scope > h1");
      if (titre) entete.insertBefore(bloc, titre.nextSibling);
      else entete.appendChild(bloc);
    }
  }

  function demarrer() {
    fetch("/api/tables/?systeme=1", {credentials: "same-origin"})
      .then(function (r) { return r.ok ? r.json() : {tables: []}; })
      .then(function (d) { var t = d.tables || []; if (t.length) placer(construire(t)); })
      .catch(function () { /* facultatif : la page fonctionne sans le sélecteur */ });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", demarrer); else demarrer();
})();
