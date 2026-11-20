// ══════════════════════════════════════════════════════════
// TÂCHES ACTIVES — reprise à la reconnexion
// ══════════════════════════════════════════════════════════
(function(){
  async function verifierTachesActives(){
    let d;
    try{ d = await api("/api/taches-actives/"); } catch(e){ return; }

    for(const t of (d.imports||[])){
      if(t.statut === "en_attente" || t.statut === "en_cours"){
        _afficherToastImport("resume-"+t.id, t.nom_fichier, t.progression);
        _pollerImportResume(t.id, t.nom_fichier);
      } else if(t.statut === "termine" && t.table_id){
        notifierSucces(tj("engine/static/engine/js/index_4.js:14:25") + t.nom_fichier);
        await rafraichirTables();
      }
    }

    const enCours = [...(d.analyses||[]), ...(d.documents||[])];
    if(enCours.length > 0){
      const noms = [...new Set(enCours.map(x => x.table_nom))].join(", ");
      const banner = document.createElement("div");
      banner.style.cssText = "position:fixed;top:0;left:0;right:0;background:var(--accent);color:var(--texte-sombre,#fff);font-size:12px;padding:6px 12px;z-index:10000;display:flex;align-items:center;gap:8px";
      banner.innerHTML = tj("engine/static/engine/js/index_4.js:24:27") + noms +
        " <button onclick='this.parentElement.remove()' style='margin-left:auto;background:transparent;border:none;color:var(--texte-sombre,#fff);cursor:pointer;font-size:14px'>✕</button>";
      document.body.prepend(banner);
      setTimeout(() => banner.remove(), 8000);
    }
  }

  async function _pollerImportResume(tacheId, nomFichier){
    let tentatives = 0;
    while(tentatives < 360){
      await new Promise(r => setTimeout(r, 2000));
      tentatives++;
      let d;
      try{ d = await api("/api/import/" + tacheId + "/statut/"); } catch(e){ continue; }
      _afficherToastImport("resume-"+tacheId, nomFichier, d.progression);
      if(d.fini){
        _supprimerToastImport("resume-"+tacheId);
        if(d.statut === "termine" && d.table){
          notifierSucces(_messageImportTermine(d));
          await rafraichirTables();
        } else if(d.erreur){
          notifierErreur(d.erreur);
        }
        return;
      }
    }
    _supprimerToastImport("resume-"+tacheId);
  }

  setTimeout(verifierTachesActives, 1500);
})();
