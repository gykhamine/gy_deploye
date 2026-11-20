/* FILTRE DE TABLE POUR LES LISTES « TABLES + COLONNES » — remplace la longue liste de toutes les tables par UN sélecteur.
 *
 * Les pages Statistiques, Analyses (ANOVA, factorielle…), Séries temporelles et Géolocalisation proposaient chaque table de l'entreprise
 * avec toutes ses colonnes, les unes sous les autres : illisible dès qu'il y a beaucoup de tables. Ici on choisit une table dans une liste
 * déroulante (avec une recherche) et SEULES ses colonnes apparaissent. Les blocs des autres tables restent dans la page (masqués) : ce qui
 * y a été coché est donc conservé et continue d'être envoyé au calcul — on peut cocher des colonnes dans plusieurs tables, en changeant
 * de table dans le filtre. Le nombre de colonnes cochées est rappelé à côté du nom de chaque table.
 *
 * Utilisation : GYFiltreTables.installer(zone)  — zone = conteneur dont les enfants directs sont un bloc par table
 *   (.st-table ou .geo-table[data-table]). Le module trouve tout seul l'identifiant (data-table), le nom et les cases cochées.
 * Le bloc #geo-tables (page Géolocalisation) est installé automatiquement.
 */
(function () {
  "use strict";
  var CSS = ".gy-ft{display:flex;flex-direction:column;gap:4px;margin:4px 0 8px}" +
    ".gy-ft input,.gy-ft select{width:100%;box-sizing:border-box;font-size:12px}" +
    ".gy-ft-resume{font-size:11px;color:var(--muted)}";
  function css() {
    if (document.getElementById("gy-ft-css")) return;
    var s = document.createElement("style"); s.id = "gy-ft-css"; s.textContent = CSS; document.head.appendChild(s);
  }
  function norme(s) { return String(s == null ? "" : s).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase(); }

  function idDe(bloc) {
    if (bloc.dataset && bloc.dataset.table) return bloc.dataset.table;
    var e = bloc.querySelector("[data-table],[data-tout],[data-expl]");
    return e ? (e.getAttribute("data-table") || e.getAttribute("data-tout") || e.getAttribute("data-expl")) : "";
  }
  function nomDe(bloc) {
    var n = bloc.querySelector(".geo-table-nom, b"), t = idDe(bloc);
    if (n) {
      var txt = n.firstChild && n.firstChild.nodeType === 3 ? n.firstChild.textContent : n.textContent;   // .geo-table-nom : le nom, puis un <span> « (ouverte) »
      t = txt.replace(/\s+/g, " ").replace(/\s*\(ouverte\)\s*$/, "").trim() || t;
    }
    return t;
  }
  function nbCoches(bloc) {
    return bloc.querySelectorAll("input[data-table]:checked, input[data-pos]:checked").length;
  }

  function installer(zone, opt) {
    if (!zone) return null;
    opt = opt || {};
    var vieux = zone.previousElementSibling;
    if (vieux && vieux.classList && vieux.classList.contains("gy-ft")) vieux.remove();   // page redessinée : on repart de zéro
    var blocs = Array.prototype.filter.call(zone.children, function (b) { return idDe(b); });
    if (blocs.length < 2) return null;             // une seule table : rien à filtrer
    css();
    var ouverte = String(opt.courant != null ? opt.courant : (window.STATS && STATS.tableId) || (window.GEO && GEO.tableId) || (window.SERIES && SERIES.tableId) || "");
    var choisie = blocs.some(function (b) { return idDe(b) === ouverte; }) ? ouverte : idDe(blocs[0]);

    var boite = document.createElement("div"); boite.className = "gy-ft";
    var rech = document.createElement("input"); rech.type = "search"; rech.placeholder = tj("engine/static/engine/js/filtre_table_liste.js:53:91"); rech.setAttribute("aria-label", tj("engine/static/engine/js/filtre_table_liste.js:53:146"));
    var sel = document.createElement("select"); sel.setAttribute("aria-label", tj("engine/static/engine/js/filtre_table_liste.js:54:81"));
    var resume = document.createElement("div"); resume.className = "gy-ft-resume";
    boite.appendChild(rech); boite.appendChild(sel); boite.appendChild(resume);
    zone.parentNode.insertBefore(boite, zone);

    function etiquette(b) {
      var n = nbCoches(b);
      return nomDe(b) + (idDe(b) === ouverte ? " (ouverte)" : "") + (n ? "  ·  " + n + " ✓" : "");
    }
    function options() {
      var q = norme(rech.value), html = [], vu = false;
      blocs.forEach(function (b) {
        var id = idDe(b);
        if (q && norme(nomDe(b)).indexOf(q) < 0 && id !== choisie) return;   // la table affichée reste toujours listée
        var o = document.createElement("option"); o.value = id; o.textContent = etiquette(b); if (id === choisie) { o.selected = true; vu = true; }
        html.push(o);
      });
      sel.textContent = ""; html.forEach(function (o) { sel.appendChild(o); });
      if (!vu && sel.options.length) { choisie = sel.options[0].value; sel.options[0].selected = true; }
    }
    function afficher() {
      blocs.forEach(function (b) { b.style.display = idDe(b) === choisie ? "" : "none"; });
      var total = blocs.reduce(function (s, b) { return s + nbCoches(b); }, 0);
      var tables = blocs.filter(function (b) { return nbCoches(b) > 0; }).length;
      resume.textContent = total ? total + tj("engine/static/engine/js/filtre_table_liste.js:78:45") + tables + " table(s)" : "";
    }
    function tout() { options(); afficher(); }
    sel.addEventListener("change", function () { choisie = sel.value; afficher(); });
    rech.addEventListener("input", function () {
      options();
      // un seul résultat : on l'affiche directement
      if (sel.options.length === 1) { choisie = sel.options[0].value; afficher(); }
    });
    zone.addEventListener("change", function () { setTimeout(tout, 0); });   // compteurs à jour après chaque case cochée/décochée
    zone.addEventListener("click", function () { setTimeout(tout, 0); });
    tout();
    return {rafraichir: tout};
  }

  window.GYFiltreTables = {installer: installer};

  function auto() { var z = document.getElementById("geo-tables"); if (z) installer(z, {courant: window.GEO && GEO.tableId}); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", auto); else auto();
})();
