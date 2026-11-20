// ══════════════════════════════════════════════════════════
// COMPTEUR DE TOKENS IA
// ══════════════════════════════════════════════════════════
(function(){
  let _tokensData = {};

  function fmt(n){ if(n >= 1e6) return (n/1e6).toFixed(1)+"M"; if(n >= 1e3) return (n/1e3).toFixed(1)+"k"; return String(n); }

  function majWidgetTokens(d){
    _tokensData = d;
    document.getElementById("tokens-total").textContent = fmt(d.tokens_total);
    document.getElementById("tokens-periode").textContent = d.periode;
    document.getElementById("tokens-in").textContent  = fmt(d.tokens_entree);
    document.getElementById("tokens-out").textContent = fmt(d.tokens_sortie);
    const alerte = document.getElementById("tokens-alerte");
    alerte.style.display = d.alerte_active ? "inline" : "none";
    // barre de progression
    const pct = d.alerte_seuil > 0 ? Math.min(100, Math.round(d.tokens_total / d.alerte_seuil * 100)) : 0;
    const barre = document.getElementById("tokens-barre");
    barre.style.width = pct + "%";
    barre.style.background = d.alerte_active ? gyCouleur("--texte-erreur","#f87171") : "var(--accent)";
  }

  function chargerTokens(){
    fetch("/api/tokens/statut/", {headers:{"X-CSRFToken": getCookie("gy_csrftoken")}})
      .then(r => r.ok ? r.json() : null)
      .then(d => { if(d) majWidgetTokens(d); })
      .catch(() => {});
  }

  window.ouvrirModalTokens = function(){
    const m = document.getElementById("modal-tokens-fond");
    m.style.display = "flex";
    chargerTokens();
    setTimeout(() => {
      const d = _tokensData;
      if(!d.tokens_total && d.tokens_total !== 0) return;
      document.getElementById("modal-tok-total").textContent  = fmt(d.tokens_total);
      document.getElementById("modal-tok-detail").textContent = fmt(d.tokens_entree)+" / "+fmt(d.tokens_sortie);
      document.getElementById("sel-tokens-periode").value = d.periode || "mois";
      document.getElementById("inp-tokens-seuil").value  = d.alerte_seuil || "";
      const debut = d.debut_periode ? new Date(d.debut_periode).toLocaleString() : "";
      document.getElementById("tokens-debut-label").textContent = debut ? tj("engine/static/engine/js/index_5.js:43:76")+debut : "";
    }, 300);
  };

  window.fermerModalTokens = function(){
    document.getElementById("modal-tokens-fond").style.display = "none";
  };

  window.sauvegarderTokensConfig = function(){
    const periode = document.getElementById("sel-tokens-periode").value;
    const seuil   = parseInt(document.getElementById("inp-tokens-seuil").value) || 0;
    fetch("/api/tokens/configurer/", {
      method: "POST",
      headers: {"Content-Type":"application/json","X-CSRFToken":getCookie("gy_csrftoken")},
      body: JSON.stringify({periode, alerte_seuil: seuil})
    }).then(r => r.json()).then(d => {
      if(d.ok){ afficherToast(tj("engine/static/engine/js/index_5.js:59:32")); chargerTokens(); fermerModalTokens(); }
      else afficherToast(d.erreur || tj("engine/static/engine/js/index_5.js:60:39"), "erreur");
    });
  };

  window.reinitialiserTokens = async function(){
    if(!await GY.confirmer(tj("engine/static/engine/js/index_5.js:65:18"))) return;
    fetch("/api/tokens/reinitialiser/", {
      method: "POST",
      headers: {"X-CSRFToken":getCookie("gy_csrftoken")}
    }).then(r => r.json()).then(d => {
      if(d.ok){ afficherToast(tj("engine/static/engine/js/index_5.js:70:32")); chargerTokens(); fermerModalTokens(); }
      else afficherToast(d.erreur || tj("engine/static/engine/js/index_5.js:71:39"), "erreur");
    });
  };

  // Fermer modal au clic sur fond
  document.getElementById("modal-tokens-fond").addEventListener("click", function(e){
    if(e.target === this) fermerModalTokens();
  });

  // Chargement initial + poll toutes les 30 s
  chargerTokens();
  setInterval(chargerTokens, 30000);
})();
