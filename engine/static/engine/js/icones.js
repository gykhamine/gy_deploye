// Icônes d'agentgy — FICHIER GÉNÉRÉ par patchs/agentgy_icones.py (carte emoji → icône symbolique Adwaita,
// la même que GSCode). Remplace chaque emoji affiché par l'icône du sprite static/engine/icons/icones.svg,
// y compris dans le contenu ajouté plus tard par le JavaScript (MutationObserver). Les listes déroulantes,
// info-bulles, titres et boîtes natives (alert/confirm) ne peuvent pas contenir d'image : l'emoji y est retiré.
(() => {
  if (window.gyIcones) return;
  const CARTE = {"⏳": "content-loading-symbolic", "⏹": "media-playback-stop-symbolic", "⏺": "media-record-symbolic", "⏻": "system-shutdown-symbolic", "▲": "go-up-symbolic", "▶": "media-playback-start-symbolic", "▸": "pan-end-symbolic", "▼": "go-down-symbolic", "▾": "pan-down-symbolic", "◈": "view-app-grid-symbolic", "☰": "open-menu-symbolic", "⚙": "applications-system-symbolic", "⚠": "dialog-warning-symbolic", "⛓": "insert-link-symbolic", "⛶": "view-fullscreen-symbolic", "✅": "emblem-ok-symbolic", "✎": "document-edit-symbolic", "✏": "document-edit-symbolic", "✓": "object-select-symbolic", "✕": "window-close-symbolic", "✖": "window-close-symbolic", "✗": "window-close-symbolic", "✨": "starred-symbolic", "➕": "list-add-symbolic", "⬆": "go-up-symbolic", "⬇": "go-down-symbolic", "🌐": "network-wireless-symbolic", "🎙": "audio-input-microphone-symbolic", "🎨": "applications-graphics-symbolic", "🎬": "camera-video-symbolic", "🎯": "find-location-symbolic", "🎵": "audio-x-generic-symbolic", "🏢": "network-workgroup-symbolic", "🏦": "auth-smartcard-symbolic", "🏷": "document-properties-symbolic", "👤": "avatar-default-symbolic", "👥": "system-users-symbolic", "💳": "auth-smartcard-symbolic", "💼": "x-office-address-book-symbolic", "💾": "document-save-symbolic", "📁": "folder-symbolic", "📄": "text-x-generic-symbolic", "📈": "x-office-spreadsheet-symbolic", "📊": "x-office-spreadsheet-symbolic", "📋": "edit-copy-symbolic", "📌": "view-pin-symbolic", "📍": "mark-location-symbolic", "📎": "mail-attachment-symbolic", "📜": "utilities-terminal-symbolic", "📝": "text-editor-symbolic", "📥": "document-open-symbolic", "📦": "package-x-generic-symbolic", "📩": "mail-unread-symbolic", "📱": "phone-symbolic", "📷": "camera-photo-symbolic", "📸": "camera-photo-symbolic", "🔁": "view-refresh-symbolic", "🔄": "view-refresh-symbolic", "🔌": "network-wired-symbolic", "🔍": "edit-find-symbolic", "🔎": "edit-find-symbolic", "🔐": "channel-secure-symbolic", "🔑": "dialog-password-symbolic", "🔒": "changes-prevent-symbolic", "🔔": "preferences-system-notifications-symbolic", "🔖": "bookmark-new-symbolic", "🔗": "insert-link-symbolic", "🔢": "accessories-calculator-symbolic", "🔮": "weather-clear-night-symbolic", "🕸": "emblem-shared-symbolic", "🖐": "list-drag-handle-symbolic", "🖱": "input-mouse-symbolic", "🖼": "image-x-generic-symbolic", "🗂": "view-list-symbolic", "🗑": "user-trash-symbolic", "🗺": "view-list-symbolic", "🚫": "action-unavailable-symbolic", "🛍": "emoji-objects-symbolic", "🛠": "applications-engineering-symbolic", "🤖": "applications-science-symbolic", "🤝": "emblem-shared-symbolic", "🧊": "weather-snow-symbolic", "🧑": "avatar-default-symbolic", "🧠": "applications-science-symbolic", "🧩": "view-grid-symbolic", "🧮": "accessories-calculator-symbolic", "🧾": "text-x-generic-symbolic", "🪄": "starred-symbolic", "🪪": "auth-smartcard-symbolic"};
  const cles = Object.keys(CARTE).sort((a, b) => b.length - a.length)
    .map(k => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!cles.length) return;
  const RE = new RegExp("(" + cles.join("|") + ")\\uFE0F?", "gu");
  const script = document.currentScript;
  const SPRITE = new URL("../icons/icones.svg", script ? script.src : location.href).pathname;
  const NS = "http://www.w3.org/2000/svg";
  const TEXTE_SEUL = new Set(["OPTION", "OPTGROUP", "TITLE", "TEXTAREA"]);
  const IGNORER = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "CODE", "PRE", "svg", "SVG"]);
  const ATTRS = ["title", "placeholder", "aria-label", "alt"];

  const nettoyer = t => t.replace(RE, "").replace(/\s{2,}/g, " ").trim();
  function icone(emoji) {
    const s = document.createElementNS(NS, "svg");
    s.setAttribute("class", "gy-ic");
    s.setAttribute("aria-hidden", "true");
    const u = document.createElementNS(NS, "use");
    u.setAttribute("href", SPRITE + "#" + CARTE[emoji.replace("️", "")]);
    s.appendChild(u);
    return s;
  }
  function traiterTexte(n) {
    const t = n.nodeValue;
    if (!t || !RE.test(t)) return;
    RE.lastIndex = 0;
    const p = n.parentNode;
    if (!p) return;
    if (TEXTE_SEUL.has(p.nodeName)) { n.nodeValue = nettoyer(t); return; }
    if (IGNORER.has(p.nodeName) || p.isContentEditable) return;
    const frag = document.createDocumentFragment();
    let dernier = 0;
    t.replace(RE, (m, e, pos) => {
      if (pos > dernier) frag.appendChild(document.createTextNode(t.slice(dernier, pos)));
      frag.appendChild(icone(e));
      dernier = pos + m.length;
      return m;
    });
    if (dernier < t.length) frag.appendChild(document.createTextNode(t.slice(dernier)));
    p.replaceChild(frag, n);
  }
  function traiterAttributs(el) {
    for (const a of ATTRS) {
      const v = el.getAttribute && el.getAttribute(a);
      if (v && RE.test(v)) { RE.lastIndex = 0; el.setAttribute(a, nettoyer(v)); }
      RE.lastIndex = 0;
    }
  }
  function traiter(racine) {
    if (racine.nodeType === 3) { traiterTexte(racine); return; }
    if (racine.nodeType !== 1 || IGNORER.has(racine.nodeName)) return;
    traiterAttributs(racine);
    const w = document.createTreeWalker(racine, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    const textes = [];
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      if (n.nodeType === 3) textes.push(n); else traiterAttributs(n);
    }
    textes.forEach(traiterTexte);
  }
  let fige = false;
  const obs = new MutationObserver(ms => {
    if (fige) return;
    fige = true;
    for (const m of ms) {
      if (m.type === "characterData") traiterTexte(m.target);
      else if (m.type === "attributes") traiterAttributs(m.target);
      else m.addedNodes.forEach(traiter);
    }
    fige = false;
  });
  function demarrer() {
    document.title = nettoyer(document.title);
    traiter(document.body);
    obs.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }
  // Filet de sécurité : plus aucune page n'appelle alert()/confirm()/prompt() (tout passe par dialogues.js : GY.alerter / confirmer / demander).
  // alert() est redirigé vers la boîte de la plateforme ; confirm() et prompt() sont synchrones, donc impossibles à rediriger : ils restent
  // natifs mais signalent l'oubli dans la console pour qu'on les remplace par `await GY.confirmer(…)` / `await GY.demander(…)`.
  const alerte = window.alert.bind(window), confirmer = window.confirm.bind(window), demander = window.prompt.bind(window);
  window.alert = m => (window.GY && window.GY.alerter) ? void window.GY.alerter(nettoyer(String(m ?? ""))) : alerte(nettoyer(String(m ?? "")));
  window.confirm = m => { console.error(tj("engine/static/engine/js/icones.js:87:42")); return confirmer(nettoyer(String(m ?? ""))); };
  window.prompt = (m, d) => { console.error("prompt() natif : utiliser await GY.demander(…)"); return demander(nettoyer(String(m ?? "")), d); };
  window.gyIcones = { icone, traiter, carte: CARTE };
  if (document.body) demarrer(); else document.addEventListener("DOMContentLoaded", demarrer);
})();
