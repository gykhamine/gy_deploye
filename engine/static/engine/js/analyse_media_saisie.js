/* Analyse des médias — saisie guidée du mode avancé : fonctions PURES (aucun DOM), pour qu'un décideur saisisse les arguments dans un champ chacun,
   sans écrire de Python. Convertit « ce que la personne tape » en texte d'appel pour l'API, et inversement (analyses prêtes à l'emploi).
   Testé sous node : node engine/tests_analyse_media_saisie.js */
(function (g) {
  "use strict";
  const NUM = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/,
    FN = ["abs", "len", "min", "max", "sum", "round", "int", "float", "mean", "median", "std"],
    BASE = ["seg", "mono", "data", "sr", "img", "gris", "prev", "pgris", "i", "x", "cv2", "np", "True", "False", "None"],
    connu = w => BASE.includes(w) || FN.includes(w) || /^[rb]\d+$/.test(w);

  // Découpe au niveau 0 (hors parenthèses, crochets et guillemets) : top("a, (1, 2), 'x,y'", ",") -> ["a", "(1, 2)", "'x,y'"]
  function top(s, sep) {
    const o = []; let d = 0, c = "", q = "";
    for (const ch of String(s == null ? "" : s)) {
      if (q) { c += ch; if (ch === q) q = ""; continue; }
      if (ch === '"' || ch === "'") { q = ch; c += ch; continue; }
      if ("([{".includes(ch)) d++; else if (")]}".includes(ch)) d--;
      if (ch === sep && d === 0) { o.push(c); c = ""; } else c += ch;
    }
    o.push(c);
    return o.map(x => x.trim()).filter(x => x !== "");
  }
  // « 300 ; 3000 » et « 300, 3000 » sont deux valeurs (sauf dans un texte entre guillemets)
  const separer = t => top(/["']/.test(t) ? t : t.replace(/;/g, ","), ",");

  // Ce que la personne tape -> valeur Python. Nombre : tel quel (0,5 -> 0.5) ; oui/non/aucun -> True/False/None ; plusieurs valeurs -> (a, b) ;
  // nom de valeur connu (le son, un résultat…) ou expression : tel quel ; sinon un texte (mis entre guillemets).
  function py(t) {
    t = String(t == null ? "" : t).trim();
    if (t === "") return "";
    if (NUM.test(t)) return t;
    if (/^[-+]?\d+,\d+$/.test(t)) return t.replace(",", ".");
    if (/^(true|vrai|oui)$/i.test(t)) return "True";
    if (/^(false|faux|non)$/i.test(t)) return "False";
    if (/^(none|aucun|aucune)$/i.test(t)) return "None";
    if (/^(["'])[\s\S]*\1$/.test(t) && top(t, ",").length === 1) return t;
    const morceaux = separer(t);
    if (morceaux.length > 1) return "(" + morceaux.map(py).join(", ") + ")";
    if (/^[\d\s.,()\[\]+\-*\/eE:]+$/.test(t)) return t;
    const tk = t.match(/[A-Za-z_][\w.]*/g) || [];
    if (tk.length && tk.every(w => connu(w.split(".")[0]))) return t;
    return JSON.stringify(t);
  }

  // Signature Python « (self, a, b=1, /, c=2, *args, d=3, **kw) » -> [{n, def, kind: po|pk|var|kw}] ; null si illisible. « =... » = facultatif sans valeur connue.
  function sigParams(sig) {
    const m = /^\s*\(([\s\S]*)\)\s*(->[\s\S]*)?$/.exec(sig || "");
    if (!m) return null;
    const out = []; let kw = false;
    for (const s of top(m[1], ",")) {
      if (s === "/") { out.forEach(p => { if (p.kind === "pk") p.kind = "po"; }); continue; }
      if (s === "*") { kw = true; continue; }
      if (s.startsWith("**")) continue;
      if (s.startsWith("*")) { out.push({ n: s.slice(1).split(":")[0].trim(), kind: "var" }); kw = true; continue; }
      const i = s.indexOf("="), nom = (i < 0 ? s : s.slice(0, i)).split(":")[0].trim();
      if (nom === "self" || nom === "cls" || !/^[A-Za-z_]\w*$/.test(nom)) continue;
      out.push({ n: nom, def: i < 0 ? undefined : s.slice(i + 1).trim(), kind: kw ? "kw" : "pk" });
    }
    return out;
  }

  // Valeurs saisies {nom: texte} -> texte des arguments. Obligatoires = dans l'ordre ; facultatifs = par leur nom (« nom=valeur »). Champ vide = valeur par défaut.
  function argsTexte(P, v) {
    const pos = [], kw = [];
    for (const p of P) {
      const t = String((v || {})[p.n] == null ? "" : v[p.n]).trim();
      if (p.kind === "var") { if (t) pos.push(...separer(t).map(py)); continue; }
      if (t === "") continue;
      const val = py(t);
      if (p.kind === "po" || (p.kind === "pk" && p.def === undefined)) pos.push(val); else kw.push(p.n + "=" + val);
    }
    return pos.concat(kw).join(", ");
  }

  // Texte d'arguments « mono, sr » ou « seg, crossfade=100 » -> valeurs par champ ; null si cela ne correspond pas à la signature.
  function valeursDepuisTexte(P, texte) {
    const v = {}, pos = P.filter(p => p.kind === "po" || p.kind === "pk"), reste = [], vr = P.find(p => p.kind === "var"); let k = 0;
    for (const s of top(texte || "", ",")) {
      const m = /^([A-Za-z_]\w*)\s*=(?!=)\s*([\s\S]*)$/.exec(s);
      if (m) { if (!P.some(p => p.n === m[1] && p.kind !== "var")) return null; v[m[1]] = m[2]; }
      else if (k < pos.length) v[pos[k++].n] = s;
      else if (vr) reste.push(s);
      else return null;
    }
    if (reste.length) v[vr.n] = reste.join(", ");
    return v;
  }

  // Conditions {l: [{a, mes, op, b}], lien: and|or} -> expression (mean(r1) > 0.5 and (len(r2) == 3))
  function condTexte(c) {
    const t = c.l.map(x => { const a = py(x.a) || "0", e = x.mes ? x.mes + "(" + a + ")" : a; return e + " " + x.op + " " + (py(x.b) || "0"); });
    return t.length > 1 ? t.map(s => "(" + s + ")").join(c.lien === "or" ? " or " : " and ") : t[0];
  }

  // Explication d'un argument lue dans la doc de la fonction (« nom : texte » ou « @param nom texte »), vide si absente
  function aideParam(doc, nom) {
    const a = new RegExp("(?:^|\\n)[\\s.@:]*(?:param\\s+)?" + nom + "\\s*(?:\\([^)\\n]*\\))?\\s*[:\\-–]\\s*([^\\n]{3,140})").exec(doc || ""),
      b = new RegExp("@param\\s+" + nom + "\\s+([^\\n]{3,140})").exec(doc || "");
    return ((b || a || [])[1] || "").trim();
  }

  const API = { top, py, sigParams, argsTexte, valeursDepuisTexte, condTexte, aideParam, connu };
  if (typeof module !== "undefined" && module.exports) module.exports = API; else g.GyMediaSaisie = API;
})(typeof window !== "undefined" ? window : globalThis);
