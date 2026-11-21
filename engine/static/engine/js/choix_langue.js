// Sélecteur de langue de la page de connexion : liste et nom des langues viennent de /api/langue/ (un fichier de i18n_defaults/ = une langue).
// Le choix est mémorisé (session + cookie d'un an) puis la page se recharge dans la langue choisie.
(async () => {
  const s = document.getElementById("choix-langue");
  if (!s) return;
  try {
    const d = await (await fetch("/api/langue/", {credentials: "same-origin"})).json();
    if (d.langues.length < 2) return;
    s.replaceChildren(...d.langues.map(c => new Option(d.noms[c] || c, c, false, c === d.courante)));
    s.hidden = false;
  } catch (e) { return; }
  s.addEventListener("change", async () => {
    const csrf = (document.cookie.match(/gy_csrftoken=([^;]+)/) || [])[1] || document.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
    const r = await fetch("/api/langue/", {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json", "X-CSRFToken": csrf}, body: JSON.stringify({langue: s.value})});
    if (r.ok) location.reload();
  });
})();
