// Éditeur visuel de « programme de formules » — UN SEUL éditeur pour la table dynamique (index_1.js::_ouvrirConstructeurProgramme), l'onglet des
// calculs et la définition des KPI (page BI). Il couvre les structures de Python : if / elif / else, for … range(pas) / for … in, while, else de boucle,
// break, continue, pass, return, affectation, et des conditions composées (et / ou / non, imbriquables). Format = engine/formules_programme.py.
// Usage : ProgrammeKPI.monter(conteneur, champs[{slug,nom}], programme, options?) → {lire(), vider()} ; ProgrammeKPI.references(programme) → Set des noms cités.
// options : { operateurs:[[code,libellé]] → briques de calcul (défaut : les 8 opérateurs des KPI) ; bibliotheque:[{id,nom}] → formules de la bibliothèque ;
//             variables:[nom] → autres calculs réutilisables (table de calcul) }
(function(){
  const OPS = [["somme",tj("engine/static/engine/js/programme_kpi.js:6:26")],["difference",tj("engine/static/engine/js/programme_kpi.js:6:53")],["produit",tj("engine/static/engine/js/programme_kpi.js:op_produit")],["quotient",tj("engine/static/engine/js/programme_kpi.js:op_quotient")],["moyenne",tj("engine/static/engine/js/programme_kpi.js:6:132")],["pourcentage",tj("engine/static/engine/js/programme_kpi.js:op_pourcentage")],["min",tj("engine/static/engine/js/programme_kpi.js:6:180")],["max",tj("engine/static/engine/js/programme_kpi.js:6:198")]];
  const CMP = [["==",tj("engine/static/engine/js/programme_kpi.js:7:23")],["!=",tj("engine/static/engine/js/programme_kpi.js:7:39")],[">",tj("engine/static/engine/js/programme_kpi.js:7:60")],[">=",tj("engine/static/engine/js/programme_kpi.js:7:81")],["<",tj("engine/static/engine/js/programme_kpi.js:7:109")],["<=",tj("engine/static/engine/js/programme_kpi.js:7:130")],["contient","contient"],["vide",tj("engine/static/engine/js/programme_kpi.js:7:185")],["non_vide",tj("engine/static/engine/js/programme_kpi.js:7:209")]];
  const PRE = "engine/static/engine/js/programme_kpi.js:", FR = {"calculer": "Calculer", "sur": "sur", "affecter": "Affecter", "retourner": "Retourner", "si": "Si", "alors": "alors", "sinonsi": tj("engine/static/engine/js/programme_kpi.js:10:196"), "sinon": "sinon", "sinon_boucle": tj("engine/static/engine/js/programme_kpi.js:10:242"), "faire": "faire", "pour": "Pour", "de": "de", "a": "à", "pas": "pas", "dans": "dans", "et": "et", "ou": "ou", "non": "non", "b_poser": "+ Affecter", "b_sinonsi": tj("engine/static/engine/js/programme_kpi.js:10:457"), "b_sinon_boucle": tj("engine/static/engine/js/programme_kpi.js:10:489"), "b_sinon_boucle_off": tj("engine/static/engine/js/programme_kpi.js:10:532"), "b_arreter": tj("engine/static/engine/js/programme_kpi.js:10:565"), "b_continuer": tj("engine/static/engine/js/programme_kpi.js:10:593"), "b_passer": "+ Passer", "b_retourner": "+ Retourner", "b_et": "+ et", "b_ou": "+ ou", "b_non": "non", "b_cond": "+ condition", "b_retirer_non": tj("engine/static/engine/js/programme_kpi.js:10:752"), "b_bascule": tj("engine/static/engine/js/programme_kpi.js:10:787"), "arreter": tj("engine/static/engine/js/programme_kpi.js:10:809"), "continuer": tj("engine/static/engine/js/programme_kpi.js:10:843"), "passer": tj("engine/static/engine/js/programme_kpi.js:10:886"), "ph_valeur": "valeur", "ph_defaut": tj("engine/static/engine/js/programme_kpi.js:10:939"), "c_dans": tj("engine/static/engine/js/programme_kpi.js:10:972"), "c_pas_dans": tj("engine/static/engine/js/programme_kpi.js:10:998"), "c_commence_par": tj("engine/static/engine/js/programme_kpi.js:10:1034"), "c_finit_par": tj("engine/static/engine/js/programme_kpi.js:10:1065"), "c_ne_contient_pas": tj("engine/static/engine/js/programme_kpi.js:10:1099"), "c_contient": "contient", "tip_liste": tj("engine/static/engine/js/programme_kpi.js:10:1157")};
  const T = c => { const v = tj(PRE + c); return v === PRE + c ? FR[c] : v; };   // texte du catalogue i18n, repli sur le français d'origine
  // comparateurs ajoutés pour couvrir `in`, `not in`, startswith, endswith, not contains
  CMP.splice(CMP.findIndex(c => c[0] === "contient"), 1, ["contient", T("c_contient")], ["ne_contient_pas", T("c_ne_contient_pas")], ["dans", T("c_dans")], ["pas_dans", T("c_pas_dans")], ["commence_par", T("c_commence_par")], ["finit_par", T("c_finit_par")]);
  const SANS_DROITE = ["vide", "non_vide"], BOUCLES = ["pour", "tantque"], SIMPLES = ["arreter", "continuer", "passer"];
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const liste = v => String(v || "").split(";").map(x => x.trim()).filter(Boolean);
  const cmp0 = (o = "==") => ({k:"cmp", g:"", o, d:""});                 // condition simple
  const sousChemin = (...p) => p.filter(x => x !== "").join(".");

  let montages = 0;   // identifiant de datalist unique par éditeur monté (plusieurs éditeurs peuvent coexister sur une page)
  function monter(cont, champs, programmeInitial, options){
    const o = options || {}, biblio = o.bibliotheque || [], externes = o.variables || [], idRefs = "pk-refs-" + (++montages);
    const operateurs = o.operateurs || OPS;
    let prog = importer(programmeInitial || []), compteur = prog.length, dernier = null;
    const variables = (l, s = []) => { l.forEach(e => { if(["calc", "poser", "pour"].includes(e.t)) s.push(e.var); (e.sinonsi || []).forEach(b => variables(b.alors, s)); ["alors", "sinon", "corps"].forEach(k => e[k] && variables(e[k], s)); }); return s; };
    const noeud = ch => ch.split("/").filter(Boolean).reduce((n, k) => n[k], prog);       // "0/sinonsi/1/alors/2" → étape n°2 du 2ᵉ « sinon si » de l'étape 0
    // condition : arbre {k:"cmp",g,o,d} | {k:"et"|"ou", l:[…]} | {k:"non", n:…} ; chemin « c.l.1.n » à partir de l'objet qui porte `cond`
    const ref = (ch, cp) => { let c = noeud(ch), k = "cond"; cp.split(".").slice(1).forEach(x => { c = c[k]; k = x; }); return [c, k]; };
    const nouvelle = t => t === "calc" ? {t, op:"somme", ops:"", var:"r" + (++compteur)}
      : t === "poser" ? {t, var:"x", val:""}
      : t === "si" ? {t, cond:cmp0(), alors:[], sinonsi:[], sinon:[]}
      : t === "pour" ? {t, var:"i", de:"1", a:"3", pas:"", corps:[], sinon:null}
      : t === "chaque" ? {t:"pour", var:"x", dans:"", corps:[], sinon:null}
      : t === "tantque" ? {t, cond:cmp0("<"), corps:[], sinon:null}
      : t === "retourner" ? {t, val:""} : {t};
    const css = "padding:4px 6px;font-size:12px;box-sizing:border-box;";
    const inp = (ch, k, v, w, ph = "", cp = "") => `<input list="${idRefs}" data-p="${ch}" data-k="${k}"${cp ? ` data-cp="${cp}"` : ""} value="${esc(v)}" placeholder="${esc(ph)}" style="${css}width:${w}px" autocomplete="off">`;
    const sel = (ch, k, opts, cp = "") => `<select data-p="${ch}" data-k="${k}"${cp ? ` data-cp="${cp}"` : ""} style="${css}">${opts}</select>`;
    const btn = (a, ch, txt, extra = "") => `<button type="button" data-a="${a}" data-p="${ch}" ${extra} style="font-size:11px">${txt}</button>`;
    const optsOp = v => operateurs.map(x => `<option value="${x[0]}"${x[0] === v ? " selected" : ""}>${esc(x[1])}</option>`).join("")
      + (biblio.length ? `<optgroup label="${tj("engine/static/engine/js/programme_kpi.js:41:44")}">${biblio.map(f => `<option value="f:${f.id}"${"f:" + f.id === v ? " selected" : ""}>${esc(f.nom)}</option>`).join("")}</optgroup>` : "");
    const optsCmp = v => CMP.map(x => `<option value="${x[0]}"${x[0] === v ? " selected" : ""}>${x[1]}</option>`).join("");
    // ── condition composée ──
    const cond = (n, ch, cp) => {
      const cc = x => `data-cp="${x}"`;
      if(n.k === "non") return `<b>${T("non")}</b> ( ${cond(n.n, ch, sousChemin(cp, "n"))} ) ${btn("c-non", ch, T("b_retirer_non"), cc(cp))}`;
      if(n.k === "cmp"){
        const dansGroupe = /\.l\.\d+$/.test(cp);
        return `<span style="display:inline-flex;gap:4px;flex-wrap:wrap;align-items:center">${inp(ch, "g", n.g, 100, T("ph_valeur"), cp)} ${sel(ch, "o", optsCmp(n.o), cp)} `
          + `${SANS_DROITE.includes(n.o) ? "" : inp(ch, "d", n.d, 100, ["dans", "pas_dans"].includes(n.o) ? T("tip_liste") : T("ph_valeur"), cp)} `
          + `${btn("c-et", ch, T("b_et"), cc(cp))}${btn("c-ou", ch, T("b_ou"), cc(cp))}${btn("c-non", ch, T("b_non"), cc(cp))}${dansGroupe ? btn("c-suppr", ch, "✕", cc(cp)) : ""}</span>`;
      }
      return `<span style="display:inline-flex;gap:4px;flex-wrap:wrap;align-items:center;border:1px dashed var(--border);border-radius:6px;padding:2px 6px">(`
        + n.l.map((x, i) => (i ? `<b>${T(n.k)}</b>` : "") + cond(x, ch, sousChemin(cp, "l", i))).join(" ")
        + `) ${btn("c-ajout", ch, T("b_cond"), cc(cp))}${btn("c-bascule", ch, T("b_bascule"), cc(cp))}${btn("c-non", ch, T("b_non"), cc(cp))}${/\.l\.\d+$/.test(cp) ? btn("c-suppr", ch, "✕", cc(cp)) : ""}</span>`;
    };
    const bloc = (titre, l, ch, boucle) => `<div style="margin:6px 0 0 14px;padding-left:8px;border-left:2px solid var(--border)"><div style="font-size:11px;color:var(--muted)">${titre}</div>${rendreListe(l, ch, boucle)}</div>`;
    const ajouts = (ch, boucle) => `<div style="margin:4px 0;font-size:12px">${[["calc", tj("engine/static/engine/js/programme_kpi.js:b_calcul")], ["poser", T("b_poser")], ["si", tj("engine/static/engine/js/programme_kpi.js:b_si")], ["pour", tj("engine/static/engine/js/programme_kpi.js:29:114")], ["chaque", tj("engine/static/engine/js/programme_kpi.js:29:141")], ["tantque", tj("engine/static/engine/js/programme_kpi.js:29:169")]]
      .concat(boucle ? [["arreter", T("b_arreter")], ["continuer", T("b_continuer")]] : [], [["passer", T("b_passer")], ["retourner", T("b_retourner")]])
      .map(([t, l]) => `<button type="button" data-a="ajout" data-t="${t}" data-p="${ch}">${l}</button>`).join(" ")}</div>`;
    const outils = c => ["haut:↑", "bas:↓", "suppr:✕"].map(x => `<button type="button" data-a="${x.split(":")[0]}" data-p="${c}">${x.split(":")[1]}</button>`).join(" ");
    function rendreListe(l, ch, boucle){
      return l.map((e, i) => {
        const c = `${ch}/${i}`; let tete = "", blocs = "";
        if(e.t === "calc") tete = `<b>${T("calculer")}</b> ${sel(c, "op", optsOp(e.op))} ${T("sur")} ${inp(c, "ops", e.ops, 170, tj("engine/static/engine/js/programme_kpi.js:34:109"))} → ${inp(c, "var", e.var, 70)}`;
        else if(e.t === "poser") tete = `<b>${T("affecter")}</b> ${inp(c, "var", e.var, 70)} = ${inp(c, "val", e.val, 140, T("ph_valeur"))}`;
        else if(e.t === "retourner") tete = `<b>${T("retourner")}</b> ${inp(c, "val", e.val, 140, T("ph_defaut"))}`;
        else if(SIMPLES.includes(e.t)) tete = `<b>${T(e.t)}</b>`;
        else if(e.t === "si"){
          tete = `<b>${T("si")}</b> ${cond(e.cond, c, "c")}`;
          blocs = bloc(T("alors"), e.alors, c + "/alors", boucle)
            + e.sinonsi.map((b, j) => { const cb = `${c}/sinonsi/${j}`; return `<div style="margin:6px 0 0 14px;padding-left:8px;border-left:2px solid var(--border)"><div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center"><b>${T("sinonsi")}</b> ${cond(b.cond, cb, "c")}<span style="margin-left:auto">${outils(cb)}</span></div>${bloc(T("alors"), b.alors, cb + "/alors", boucle)}</div>`; }).join("")
            + `<div style="margin:4px 0 0 14px">${btn("sinonsi", c, T("b_sinonsi"))}</div>` + bloc(T("sinon"), e.sinon, c + "/sinon", boucle);
        } else {
          tete = e.t === "tantque" ? `<b>${tj("engine/static/engine/js/programme_kpi.js:37:43")}</b> ${cond(e.cond, c, "c")}`
            : e.dans !== undefined ? `<b>${tj("engine/static/engine/js/programme_kpi.js:38:43")}</b> ${inp(c, "var", e.var, 60)} ${T("dans")} ${inp(c, "dans", e.dans, 170, tj("engine/static/engine/js/programme_kpi.js:38:115"))}`
            : `<b>${T("pour")}</b> ${inp(c, "var", e.var, 60)} ${T("de")} ${inp(c, "de", e.de, 70)} ${T("a")} ${inp(c, "a", e.a, 70)} ${T("pas")} ${inp(c, "pas", e.pas, 50, "1")}`;
          blocs = bloc(T("faire"), e.corps, c + "/corps", true)
            + (Array.isArray(e.sinon) ? bloc(T("sinon_boucle"), e.sinon, c + "/sinon", boucle) + `<div style="margin:4px 0 0 14px">${btn("sinon-off", c, T("b_sinon_boucle_off"))}</div>`
              : `<div style="margin:4px 0 0 14px">${btn("sinon-on", c, T("b_sinon_boucle"))}</div>`);
        }
        return `<div style="border:1px solid var(--border);border-radius:6px;padding:6px 8px;margin:4px 0"><div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">${tete}<span style="margin-left:auto;white-space:nowrap">${outils(c)}</span></div>${blocs}</div>`;
      }).join("") + ajouts(ch, boucle);
    }
    function dessiner(){
      dernier = null;
      const refs = [...champs.map(c => [c.slug, c.nom]), ...externes.map(n => [n, tj("engine/static/engine/js/programme_kpi.js:calcul_nom", {v1: n})]), ...[...new Set(variables(prog))].map(v => [v, tj("engine/static/engine/js/programme_kpi.js:87:149") + v])];
      cont.innerHTML = `<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px">${refs.map(([v, n]) => `<button type="button" data-a="ref" data-v="${esc(v)}" style="font-size:11px">${esc(n)}</button>`).join("")}</div>
        <datalist id="${idRefs}">${refs.map(([v, n]) => `<option value="${esc(v)}">${esc(n)}</option>`).join("")}</datalist>${rendreListe(prog, "", false)}`;
    }
    cont.onfocusin = ev => { if(ev.target.dataset && ev.target.dataset.k) dernier = ev.target; };
    cont.onchange = ev => {
      const el = ev.target; if(!el.dataset || !el.dataset.k) return;
      const [c, k] = el.dataset.cp ? ref(el.dataset.p, el.dataset.cp) : [null, null], cible = c ? c[k] : noeud(el.dataset.p);
      cible[el.dataset.k] = el.value;
      if(["op", "o", "var"].includes(el.dataset.k)) dessiner();   // libellés, champ « valeur de droite » et liste des variables à rafraîchir
    };
    // actions sur l'arbre d'une condition (clic sur un bouton « data-a=c-… » ; data-cp = chemin du nœud visé)
    function actionCondition(a, ch, cp){
      const [c, k] = ref(ch, cp), n = c[k], m = cp.match(/^(.*)\.l\.(\d+)$/), parent = m ? ref(ch, m[1]) : null, pere = parent ? parent[0][parent[1]] : null;
      if(a === "c-et" || a === "c-ou"){
        const g = a.slice(2);
        if(pere && pere.k === g) pere.l.splice(+m[2] + 1, 0, cmp0()); else c[k] = {k:g, l:[n, cmp0()]};
      } else if(a === "c-non") c[k] = n.k === "non" ? n.n : {k:"non", n};
      else if(a === "c-ajout") n.l.push(cmp0());
      else if(a === "c-bascule") n.k = n.k === "et" ? "ou" : "et";
      else if(a === "c-suppr" && pere){   // retire la condition ; un groupe réduit à une seule condition se replie
        pere.l.splice(+m[2], 1);
        if(pere.l.length === 1){ const [cc, kk] = ref(ch, m[1]); cc[kk] = pere.l[0]; }
      }
    }
    cont.onclick = ev => {
      const b = ev.target.closest("button[data-a]"); if(!b) return;
      const a = b.dataset.a, ch = b.dataset.p;
      if(a === "ref"){
        if(dernier){ dernier.value = dernier.dataset.k === "ops" && dernier.value ? dernier.value + " ; " + b.dataset.v : b.dataset.v; dernier.dispatchEvent(new Event("change", {bubbles:true})); }
        return;
      }
      if(a.startsWith("c-")){ actionCondition(a, ch, b.dataset.cp); return dessiner(); }
      if(a === "ajout"){ noeud(ch).push(nouvelle(b.dataset.t)); return dessiner(); }
      if(a === "sinonsi"){ noeud(ch).sinonsi.push({cond:cmp0(), alors:[]}); return dessiner(); }
      if(a === "sinon-on" || a === "sinon-off"){ noeud(ch).sinon = a === "sinon-on" ? [] : null; return dessiner(); }
      const i = +ch.split("/").pop(), l = noeud(ch.slice(0, ch.lastIndexOf("/")));
      if(a === "suppr") l.splice(i, 1);
      else if(a === "haut" && i > 0) [l[i-1], l[i]] = [l[i], l[i-1]];
      else if(a === "bas" && i < l.length - 1) [l[i+1], l[i]] = [l[i], l[i+1]];
      dessiner();
    };
    dessiner();
    return { lire: () => exporter(prog), vider: () => { prog = []; compteur = 0; dessiner(); } };
  }
  // modèle d'écran ⇄ programme du moteur
  const exporterCond = n => n.k === "cmp" ? {g:n.g, o:n.o, d:n.d} : n.k === "non" ? {non:exporterCond(n.n)} : {[n.k]:n.l.map(exporterCond)};
  function importerCond(c){
    c = c || {};
    if(Array.isArray(c.et)) return {k:"et", l:c.et.map(importerCond)};
    if(Array.isArray(c.ou)) return {k:"ou", l:c.ou.map(importerCond)};
    if(c.non) return {k:"non", n:importerCond(c.non)};
    return {k:"cmp", g:c.g || "", o:c.o || "==", d:c.d || ""};
  }
  function exporter(l){
    return l.map(e => {
      if(e.t === "calc") return e.op.startsWith("f:") ? {t:"calc", op:"personnalisee", formule_id:+e.op.slice(2), ops:liste(e.ops), var:e.var} : {t:"calc", op:e.op, ops:liste(e.ops), var:e.var};
      if(e.t === "poser") return {t:"poser", var:e.var, val:e.val};
      if(e.t === "retourner") return e.val !== "" ? {t:"retourner", val:e.val} : {t:"retourner"};
      if(SIMPLES.includes(e.t)) return {t:e.t};
      if(e.t === "si"){
        const s = {t:"si", cond:exporterCond(e.cond), alors:exporter(e.alors), sinon:exporter(e.sinon)};
        return e.sinonsi.length ? {...s, sinonsi:e.sinonsi.map(b => ({cond:exporterCond(b.cond), alors:exporter(b.alors)}))} : s;
      }
      const boucle = e.t === "tantque" ? {t:"tantque", cond:exporterCond(e.cond)}
        : e.dans !== undefined ? {t:"pour", var:e.var, dans:liste(e.dans)}
        : {t:"pour", var:e.var, de:e.de, a:e.a, ...(String(e.pas).trim() && String(e.pas).trim() !== "1" ? {pas:e.pas} : {})};
      return {...boucle, corps:exporter(e.corps), ...(Array.isArray(e.sinon) ? {sinon:exporter(e.sinon)} : {})};
    });
  }
  function importer(l){
    if(!Array.isArray(l)) return [];
    if(l.some(e => e && e.operateur)) return l.map((e, i) => ({t:"calc", op:e.operateur, ops:(e.operandes || []).join(" ; "), var:"r" + (i + 1)}));   // ancien format
    return l.map(e => {
      const sinon = Array.isArray(e.sinon) ? importer(e.sinon) : null;
      if(e.t === "calc") return {t:"calc", op:e.op === "personnalisee" ? "f:" + e.formule_id : e.op, ops:(e.ops || []).join(" ; "), var:e.var};
      if(e.t === "poser") return {t:"poser", var:e.var || "", val:e.val == null ? "" : String(e.val)};
      if(e.t === "retourner") return {t:"retourner", val:e.val == null ? "" : String(e.val)};
      if(SIMPLES.includes(e.t)) return {t:e.t};
      if(e.t === "si") return {t:"si", cond:importerCond(e.cond), alors:importer(e.alors), sinonsi:(e.sinonsi || []).map(b => ({cond:importerCond(b.cond), alors:importer(b.alors)})), sinon:sinon || []};
      if(e.t === "tantque") return {t:"tantque", cond:importerCond(e.cond), corps:importer(e.corps), sinon};
      return e.dans !== undefined ? {t:"pour", var:e.var, dans:(e.dans || []).join(" ; "), corps:importer(e.corps), sinon}
        : {t:"pour", var:e.var, de:String(e.de ?? 1), a:String(e.a ?? 1), pas:e.pas == null ? "" : String(e.pas), corps:importer(e.corps), sinon};
    });
  }
  // Tous les noms cités par un programme exporté (colonnes, variables, littéraux) — équivalent de formules_programme.noms_references.
  function references(l, sortie = new Set()){
    const dansCond = c => { if(!c) return; [].concat(c.et || [], c.ou || [], c.non ? [c.non] : []).forEach(dansCond); ["g", "d"].forEach(k => c[k] != null && String(c[k]).split(";").forEach(x => sortie.add(x.trim()))); };
    (l || []).forEach(e => {
      ["de", "a", "pas", "val"].forEach(k => e[k] != null && e[k] !== "" && sortie.add(String(e[k])));
      [].concat(e.ops || [], e.dans || []).forEach(x => sortie.add(String(x)));
      dansCond(e.cond); (e.sinonsi || []).forEach(b => { dansCond(b.cond); references(b.alors, sortie); });
      ["alors", "sinon", "corps"].forEach(k => references(e[k], sortie));
    });
    return sortie;
  }
  window.ProgrammeKPI = { monter, references };
})();
