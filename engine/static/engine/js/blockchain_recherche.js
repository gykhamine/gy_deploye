// Page blockchain — surlignage des termes cherchés et navigation entre correspondances, comme la recherche de l'ontologie :
// la première correspondance est mise en avant, Entrée = correspondance suivante (tant que le texte n'a pas changé), ▲ / ▼ = précédente / suivante.
// Le filtrage lui-même est fait côté serveur (blockchain_recherche.py) sur toute la chaîne, pas seulement la page affichée.
(function(){
  const main = document.querySelector("main[data-mots]"), champ = document.getElementById("rech-q");
  if(!main) return;
  let mots = [];
  try { mots = JSON.parse(main.dataset.mots || "[]").filter(Boolean); } catch(e) { /* pas de surlignage */ }
  // texte sans accents ni casse, de MÊME longueur que l'original (les index restent alignés)
  const plier = s => s.split("").map(c => c.normalize("NFD")[0].toLowerCase()).join("");
  const marques = [];
  if(mots.length){
    const motifs = mots.map(plier);
    const noeuds = [], arbre = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
    while(arbre.nextNode()) if(arbre.currentNode.nodeValue.trim() && !arbre.currentNode.parentElement.closest("script,style")) noeuds.push(arbre.currentNode);
    noeuds.forEach(n => {
      const texte = n.nodeValue, pli = plier(texte), plages = [];
      motifs.forEach(m => { for(let i = pli.indexOf(m); i >= 0; i = pli.indexOf(m, i + m.length)) plages.push([i, i + m.length]); });
      if(!plages.length) return;
      plages.sort((a, b) => a[0] - b[0]);
      const fusion = plages.reduce((r, p) => { const d = r[r.length - 1]; d && p[0] <= d[1] ? d[1] = Math.max(d[1], p[1]) : r.push(p.slice()); return r; }, []);
      const frag = document.createDocumentFragment();
      let pos = 0;
      fusion.forEach(([a, b]) => {
        frag.append(texte.slice(pos, a));
        const m = document.createElement("mark"); m.className = "rech-mark"; m.textContent = texte.slice(a, b); frag.append(m); marques.push(m); pos = b;
      });
      frag.append(texte.slice(pos));
      n.replaceWith(frag);
    });
  }
  const nav = document.querySelector(".rech-nav"), compteur = document.querySelector(".rech-compteur");
  let courant = -1;
  const aller = pas => {
    if(!marques.length) return;
    if(courant >= 0) marques[courant].classList.remove("courant");
    courant = (courant + pas + marques.length) % marques.length;
    marques[courant].classList.add("courant");
    marques[courant].scrollIntoView({block: "center", behavior: "smooth"});
    compteur.textContent = `${courant + 1} / ${marques.length}`;
  };
  if(marques.length && nav){
    nav.hidden = false;
    nav.addEventListener("click", ev => { const b = ev.target.closest("button[data-nav]"); if(b) aller(+b.dataset.nav); });
    aller(1);
  }
  if(champ) champ.addEventListener("keydown", ev => {
    // Entrée : correspondance suivante si la recherche affichée est déjà celle du champ ; sinon on lance la recherche (envoi du formulaire)
    if(ev.key === "Enter" && marques.length && champ.value.trim() === (champ.dataset.applique || "").trim()){ ev.preventDefault(); aller(ev.shiftKey ? -1 : 1); }
  });
})();
