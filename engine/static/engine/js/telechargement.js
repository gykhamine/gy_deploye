// Téléchargement unique de tout fichier généré par le serveur (PDF, ZIP) : fetch + blob + <a download>.
// window.open / location.href / <a target="_blank"> sur une réponse « attachment » (ou sur un PDF servi
// « inline » depuis /media/) ne télécharge rien dans la webview de l'app, et une erreur serveur n'y est
// jamais affichée. Tous les boutons d'export passent par gyTelecharger : une seule implémentation.
//
// gyTelecharger(url, nomFichier, {methode, bouton, succes, erreur}) -> Promise<boolean>
//   methode : "GET" (défaut) ou "POST" (jeton CSRF joint dans les deux cas)
//   bouton  : élément mis en « … » et désactivé pendant la génération, restauré ensuite
//   succes  : texte (page à notifierSucces) ou fonction appelée à la fin (facultatif)
//   erreur  : fonction(message) appelée en cas d'échec (défaut : notifierErreur, sinon toast, sinon alert)
(() => {
  const csrf = () => (document.cookie.match(/gy_csrftoken=([^;]+)/) || [])[1] || "";
  const signalerErreur = (message, perso) => {
    if (typeof perso === "function") return perso(message);
    if (typeof window.notifierErreur === "function") return window.notifierErreur(message);
    if (typeof window.toast === "function") return window.toast(message, "err");  // 'err' : truthy pour toast(msg, err=false)
    GY.alerter(message);
  };
  // succes : fonction à appeler, ou texte (affiché seulement si la page a notifierSucces : les toast() des
  // autres pages n'ont pas la même signature, leurs appelants passent alors une fonction).
  const signalerSucces = (succes) => {
    if (typeof succes === "function") return succes();
    if (succes && typeof window.notifierSucces === "function") return window.notifierSucces(succes);
  };

  // Enregistre un Blob déjà en mémoire (CSV construit côté navigateur, etc.). Lien ajouté au DOM puis retiré,
  // révocation différée : un lien détaché + révocation immédiate échoue dans certains WebKit.
  window.gyTelechargerBlob = function gyTelechargerBlob(blob, nomFichier) {
    const lien = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = lien; a.download = nomFichier; a.style.display = "none";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(lien), 60000);
  };

  // Types que le navigateur sait afficher dans un onglet (PDF, images, HTML, texte) ; un ZIP ou un tableur ne se « lit » pas : pas de bouton.
  const ouvrable = (blob) => /^(application\/pdf|image\/|text\/html|text\/plain|application\/json)/i.test((blob && blob.type) || "");
  const K = "engine/static/engine/js/telechargement.js:";

  // Notification de fin de génération avec un bouton « Ouvrir dans un nouvel onglet » (le fichier est déjà téléchargé ; ceci l'affiche en plus).
  // Indépendante de la page (toast propre) : fonctionne partout où gyTelecharger est utilisé.
  window.gyNotifierFichierPret = function gyNotifierFichierPret(blob, message) {
    const url = URL.createObjectURL(blob);
    setTimeout(() => URL.revokeObjectURL(url), 10 * 60 * 1000);
    const ancien = document.getElementById("gy-toast-fichier"); if (ancien) ancien.remove();
    const boite = document.createElement("div");
    boite.id = "gy-toast-fichier"; boite.setAttribute("role", "status");
    boite.style.cssText = "position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:100001;display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:center;max-width:calc(100vw - 24px);padding:8px 12px;border-radius:8px;font-size:13px;background:var(--panel,#101820);color:var(--text,#e5e7eb);border:1px solid var(--accent,#38bdf8);box-shadow:0 4px 24px rgba(0,0,0,.45)";
    const texte = document.createElement("span"); texte.textContent = message || tj(K + "fichier_pret");
    const ouvrir = document.createElement("button"); ouvrir.type = "button"; ouvrir.textContent = tj(K + "ouvrir_onglet");
    ouvrir.style.cssText = "cursor:pointer;font-size:12px;padding:4px 10px;border-radius:6px;border:1px solid var(--accent,#38bdf8);background:transparent;color:var(--accent,#38bdf8)";
    ouvrir.onclick = () => { window.open(url, "_blank", "noopener"); boite.remove(); };
    const fermer = document.createElement("button"); fermer.type = "button"; fermer.textContent = "✕"; fermer.title = tj(K + "fermer");
    fermer.setAttribute("aria-label", tj(K + "fermer"));
    fermer.style.cssText = "cursor:pointer;font-size:12px;padding:2px 6px;border:none;background:transparent;color:inherit";
    fermer.onclick = () => boite.remove();
    boite.append(texte, ouvrir, fermer); document.body.appendChild(boite);
    setTimeout(() => boite.remove(), 15000);
  };

  window.gyTelecharger = async function gyTelecharger(url, nomFichier, options) {
    const { methode = "GET", bouton = null, succes = "", erreur = null } = options || {};
    const contenuInitial = bouton ? bouton.innerHTML : "";
    if (bouton) { bouton.textContent = "…"; bouton.disabled = true; }
    try {
      const r = await fetch(url, {
        method: methode, credentials: "same-origin",
        headers: { "X-CSRFToken": csrf() },
      });
      if (!r.ok) {
        const donnees = await r.json().catch(() => ({}));
        signalerErreur(donnees.erreur || tj("engine/static/engine/js/telechargement.js:47:43"), erreur);
        return false;
      }
      // Nom choisi par le serveur (extension exacte : .csv, .xlsx ou .json selon le format réellement produit).
      const m = /filename="?([^";]+)"?/i.exec(r.headers.get("Content-Disposition") || "");
      const blob = await r.blob();
      window.gyTelechargerBlob(blob, m ? m[1] : nomFichier);
      if (ouvrable(blob)) window.gyNotifierFichierPret(blob, typeof succes === "string" ? succes : "");   // texte de succès + bouton d'ouverture dans un onglet
      else signalerSucces(succes);
      if (ouvrable(blob) && typeof succes === "function") succes();
      return true;
    } catch (e) {
      signalerErreur(tj("engine/static/engine/js/telechargement.js:56:23"), erreur);
      return false;
    } finally {
      if (bouton) { bouton.innerHTML = contenuInitial; bouton.disabled = false; }
    }
  };
})();
