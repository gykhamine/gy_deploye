// Saisie visuelle des champs « vecteur » et « matrice » : une vraie grille de cases à remplir, aucune syntaxe à connaître.
// Le <textarea id=…> (masqué) reste la SOURCE DE VÉRITÉ pour le reste du code (validation, collecte, envoi) : il contient le JSON
// ([1, 2] ou [[1, 2], [3, 4]]) recalculé à chaque frappe — l'utilisateur ne le voit jamais. Le DOM de la grille est son propre état,
// donc le même balisage sert partout (formulaire agent, espace client, flux média) via une délégation d'événements unique.
// Forme configurée sur le champ ("3,4" ou "10") -> grille de taille fixe ; sinon grille libre (boutons ajouter / retirer).
// Une case vide vaut 0. Les valeurs collées depuis Excel / un tableur (tabulations, retours à la ligne) ou un JSON Python sont acceptées.
(function () {
  const K = "engine/static/engine/js/champ_tenseur.js:";
  const T = (cle, repli) => { try { const v = typeof tj === "function" ? tj(K + cle) : ""; return v && v !== K + cle ? v : repli; } catch (e) { return repli; } };
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const MAX_CASES = 2500;                       // au-delà, une grille n'est plus lisible : on garde la zone de texte d'origine
  const DEFAUT = { vecteur: [1, 3], matrice: [3, 3] };   // [lignes, colonnes] d'une grille libre neuve

  // "3,4" -> [3, 4] ; "10" (vecteur) -> [1, 10] ; vide ou invalide -> null (grille libre)
  function dimensions(type, forme) {
    const d = String(forme || "").split(",").map((x) => parseInt(x, 10)).filter((n) => n > 0);
    if (!d.length) return null;
    return type === "vecteur" ? [1, d[0]] : [d[0], d[1] || d[0]];
  }
  const cellule = (v) => `<td><input class="gy-tenseur-cell" type="text" inputmode="decimal" autocomplete="off" placeholder="0" value="${v == null ? "" : esc(v)}"></td>`;

  // matrice de chaînes -> tableau HTML (en-têtes 1…n, sans texte à traduire)
  function grille(type, m) {
    const nc = m[0].length;
    const tete = Array.from({ length: nc }, (_, j) => `<th scope="col">${j + 1}</th>`).join("");
    const corps = m.map((r, i) => `<tr>${type === "matrice" ? `<th scope="row">${i + 1}</th>` : ""}${r.map(cellule).join("")}</tr>`).join("");
    return `<table class="gy-tenseur-grille"><thead><tr>${type === "matrice" ? "<th></th>" : ""}${tete}</tr></thead><tbody>${corps}</tbody></table>`;
  }
  const vide = (l, c) => Array.from({ length: l }, () => Array(c).fill(""));
  const bouton = (act, texte) => `<button type="button" class="gy-tenseur-btn" data-gy-tenseur-act="${act}">${esc(texte)}</button>`;

  // HTML complet du champ. `attrs` : attributs supplémentaires du textarea (ex. data-slug="…" data-type="…" pour le flux média).
  function html(id, type, forme, attrs) {
    const dims = dimensions(type, forme), init = dims || DEFAUT[type] || DEFAUT.matrice;
    if (init[0] * init[1] > MAX_CASES) return `<textarea id="${esc(id)}" ${attrs || ""} rows="3" style="font-family:ui-monospace,monospace;font-size:12px"></textarea>`;
    const barre = dims ? "" : `<div class="gy-tenseur-barre">${type === "matrice"
      ? bouton("lig+", T("ligne_plus", "+ Ligne")) + bouton("lig-", T("ligne_moins", "− Ligne")) + bouton("col+", T("colonne_plus", "+ Colonne")) + bouton("col-", T("colonne_moins", "− Colonne"))
      : bouton("col+", T("case_plus", "+ Case")) + bouton("col-", T("case_moins", "− Case"))}</div>`;
    return `<div class="gy-tenseur" data-type="${type}" data-fixe="${dims ? 1 : 0}" data-init="${init.join(",")}">
      <div class="gy-tenseur-defil">${grille(type, vide(init[0], init[1]))}</div>${barre}
      <div class="gy-tenseur-aide"><span class="gy-tenseur-dim"></span> · ${esc(T("aide", "Une case vide vaut 0. Vous pouvez coller des valeurs depuis Excel."))}</div>
      <textarea id="${esc(id)}" ${attrs || ""} hidden></textarea></div>`;
  }

  const lecture = (w) => [...w.querySelectorAll("tbody tr")].map((tr) => [...tr.querySelectorAll("input")].map((i) => i.value));
  const nombre = (v) => { const n = parseFloat(String(v).replace(",", ".")); return Number.isFinite(n) ? n : 0; };

  // grille -> JSON dans le textarea masqué
  function synchroniser(w) {
    const m = lecture(w), ta = w.querySelector("textarea");
    const tout_vide = m.every((r) => r.every((v) => v.trim() === ""));
    const nums = m.map((r) => r.map(nombre));
    const sortie = tout_vide ? "" : JSON.stringify(w.dataset.type === "vecteur" ? nums[0] : nums);
    const dim = w.querySelector(".gy-tenseur-dim");
    if (dim) dim.textContent = w.dataset.type === "vecteur" ? String(m[0].length) : `${m.length} × ${m[0].length}`;
    if (ta.value !== sortie) { ta.value = sortie; ta.dispatchEvent(new Event("input", { bubbles: true })); }
  }
  function rendre(w, m, focus) {
    w.querySelector(".gy-tenseur-defil").innerHTML = grille(w.dataset.type, m);
    if (focus) { const c = w.querySelectorAll("tbody tr")[focus[0]]?.querySelectorAll("input")[focus[1]]; if (c) c.focus(); }
    synchroniser(w);
  }

  // Préremplit (ou remet à zéro avec null) : `valeur` = liste / liste de listes déjà décodée, ou JSON en texte.
  function remplir(el, valeur) {
    if (typeof el === "string") el = document.getElementById(el);
    const w = el && el.closest && el.closest(".gy-tenseur");
    if (!w) { if (el) el.value = valeur == null ? "" : typeof valeur === "string" ? valeur : JSON.stringify(valeur); return; }
    let v = valeur;
    if (typeof v === "string") { try { v = v.trim() === "" ? null : JSON.parse(v); } catch (e) { v = null; } }
    let m = null;
    if (Array.isArray(v) && v.length) {
      m = w.dataset.type === "vecteur" ? [v.flat(Infinity)] : Array.isArray(v[0]) ? v : [v];
      const nc = Math.max(1, ...m.map((r) => r.length));
      m = m.map((r) => Array.from({ length: nc }, (_, j) => (r[j] == null ? "" : String(r[j]))));   // la grille s'adapte aux données : rien n'est perdu
    }
    if (!m) { const [l, c] = w.dataset.init.split(",").map(Number); m = vide(l, c); }
    rendre(w, m);
  }

  function action(w, act) {
    let m = lecture(w), f = null;
    const nc = m[0].length;
    if (act === "lig+" && (m.length + 1) * nc <= MAX_CASES) { m.push(Array(nc).fill("")); f = [m.length - 1, 0]; }
    else if (act === "lig-" && m.length > 1) m.pop();
    else if (act === "col+" && m.length * (nc + 1) <= MAX_CASES) { m.forEach((r) => r.push("")); f = [0, nc]; }
    else if (act === "col-" && nc > 1) m.forEach((r) => r.pop());
    else return;
    rendre(w, m, f);
  }

  // Collage : JSON Python/JSON ("[[1,2],[3,4]]") ou bloc de tableur (tabulations / ; / retours à la ligne), à partir de la case active.
  function coller(e, cel) {
    const w = cel.closest(".gy-tenseur"), txt = (e.clipboardData || window.clipboardData).getData("text");
    if (/^\s*\[/.test(txt)) { try { const j = JSON.parse(txt); e.preventDefault(); remplir(w.querySelector("textarea"), j); return; } catch (x) { /* pas du JSON : collage normal */ } }
    const lignes = txt.replace(/\r/g, "").replace(/\n+$/, "").split("\n").map((l) => l.split(/\t|;/));
    if (lignes.length < 2 && lignes[0].length < 2) return;   // une seule valeur : collage normal dans la case
    e.preventDefault();
    const m = lecture(w), tr = cel.closest("tr"), r0 = [...tr.parentNode.children].indexOf(tr), c0 = [...tr.querySelectorAll("input")].indexOf(cel);
    const fixe = w.dataset.fixe === "1", vect = w.dataset.type === "vecteur";
    const plat = vect ? [lignes.flat()] : lignes;
    plat.forEach((l, i) => l.forEach((x, j) => {
      const R = r0 + i, C = c0 + j;
      if (R >= m.length) { if (fixe || vect) return; while (m.length <= R) m.push(Array(m[0].length).fill("")); }
      if (C >= m[0].length) { if (fixe) return; m.forEach((r) => { while (r.length <= C) r.push(""); }); }
      m[R][C] = x.trim();
    }));
    rendre(w, m);
  }

  document.addEventListener("input", (e) => {
    const c = e.target; if (!c.classList || !c.classList.contains("gy-tenseur-cell")) return;
    const propre = c.value.replace(/[^0-9eE+\-.,]/g, "");   // seuls les chiffres, le signe, la virgule/point et l'exposant passent
    if (propre !== c.value) c.value = propre;
    synchroniser(c.closest(".gy-tenseur"));
  });
  document.addEventListener("focusout", (e) => {   // à la sortie de la case : affichage normalisé (1,50 -> 1.5), valeur illisible effacée
    const c = e.target; if (!c.classList || !c.classList.contains("gy-tenseur-cell") || c.value.trim() === "") return;
    const n = parseFloat(c.value.replace(",", "."));
    c.value = Number.isFinite(n) ? String(n) : "";
    synchroniser(c.closest(".gy-tenseur"));
  });
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("[data-gy-tenseur-act]");
    if (b) action(b.closest(".gy-tenseur"), b.dataset.gyTenseurAct);
  });
  document.addEventListener("paste", (e) => { const c = e.target; if (c.classList && c.classList.contains("gy-tenseur-cell")) coller(e, c); });
  document.addEventListener("keydown", (e) => {   // Entrée / ↓ : case du dessous ; ↑ : case du dessus (comme dans un tableur)
    const c = e.target; if (!c.classList || !c.classList.contains("gy-tenseur-cell") || !["Enter", "ArrowDown", "ArrowUp"].includes(e.key)) return;
    const tr = c.closest("tr"), j = [...tr.querySelectorAll("input")].indexOf(c);
    const voisin = e.key === "ArrowUp" ? tr.previousElementSibling : tr.nextElementSibling;
    const cible = voisin && voisin.querySelectorAll("input")[j];
    if (cible) { e.preventDefault(); cible.focus(); cible.select(); }
    else if (e.key === "Enter") e.preventDefault();   // pas de soumission du formulaire par erreur depuis une case
  });

  window.GYTenseur = { html, remplir, dimensions };
  if (typeof module !== "undefined" && module.exports) module.exports = window.GYTenseur;
})();
