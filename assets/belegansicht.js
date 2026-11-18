// Große Belegansicht direkt im Browser – öffnet sofort, ohne dass Streamlit die Seite neu berechnet.
// Greift Klicks auf das Vorschaubild (.st-key-bildklick_<key>) und „Größer ansehen“ (.st-key-gross_<key>) ab,
// bevor Streamlit sie bekommt, und zeigt alle Seiten aus .st-key-belegbild_<key> / .st-key-belegmehr_<key>.
// Fehlt etwas (keine Bilder gefunden), läuft der Klick normal weiter -> Streamlit-Fenster als Rückfall.
(function () {
  if (window.__belegAnsicht) return;
  window.__belegAnsicht = true;

  const stil = document.createElement("style");
  stil.textContent = `
    .beleg-gross {position: fixed; inset: 0; z-index: 1000000; background: rgba(20, 21, 26, .82);
      overflow-y: auto; display: flex; flex-direction: column; align-items: center; gap: 18px;
      padding: 56px 16px 32px; cursor: zoom-out; animation: beleg-gross-ein .12s ease-out;}
    @keyframes beleg-gross-ein {from {opacity: 0;} to {opacity: 1;}}
    .beleg-gross figure {margin: 0; max-width: 100%; text-align: center;}
    .beleg-gross .beleg-gross-bild {position: relative; display: inline-block; min-width: 120px; min-height: 120px;
      background: #fff; border-radius: 8px; box-shadow: 0 10px 40px rgba(0, 0, 0, .35); overflow: hidden;
      vertical-align: top;}
    /* jede Seite passt ganz in den sichtbaren Bereich (Höhe), breite Seiten höchstens Bildschirmbreite */
    .beleg-gross img {display: block; width: auto; height: auto; max-height: calc(100vh - 110px);
      max-width: min(1100px, calc(100vw - 32px)); opacity: 0; transition: opacity .15s;}
    .beleg-gross img.geladen {opacity: 1;}
    .beleg-gross .beleg-gross-bild:not(.fertig)::after {content: ""; position: absolute; top: 50%; left: 50%;
      width: 34px; height: 34px; margin: -17px 0 0 -17px; border-radius: 50%; border: 3px solid #E5E7EB;
      border-top-color: #C25100; animation: beleg-gross-dreh .8s linear infinite;}
    @keyframes beleg-gross-dreh {to {transform: rotate(360deg);}}
    .beleg-gross figcaption {color: #fff; font: 500 .9rem "Source Sans", sans-serif; margin-top: 8px;}
    .beleg-gross-zu {position: fixed; top: 12px; right: 16px; width: 40px; height: 40px; border-radius: 50%;
      border: none; background: #fff; color: #14151A; font-size: 24px; line-height: 40px; cursor: pointer;
      box-shadow: 0 2px 10px rgba(0, 0, 0, .3);}
    .beleg-gross-zu:focus-visible {outline: 3px solid #C25100; outline-offset: 2px;}
    .beleg-gross-hinweis {position: fixed; top: 18px; left: 50%; transform: translateX(-50%); color: #fff;
      font: .9rem "Source Sans", sans-serif; opacity: .85; pointer-events: none;}
  `;
  document.head.appendChild(stil);

  let offen = null;
  let vorher = null;  // Fokus vor dem Öffnen (danach wiederherstellen)

  function schliessen() {
    if (!offen) return;
    offen.remove();
    offen = null;
    document.removeEventListener("keydown", taste, true);
    if (vorher && vorher.focus) vorher.focus();
  }

  function taste(ev) {
    if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); schliessen(); }
  }

  function oeffnen(quellen) {
    schliessen();
    vorher = document.activeElement;
    const box = document.createElement("div");
    box.className = "beleg-gross";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    box.setAttribute("aria-label", "Beleg in voller Größe");
    const hinweis = document.createElement("div");
    hinweis.className = "beleg-gross-hinweis";
    hinweis.textContent = "Klick oder Esc schließt";
    box.appendChild(hinweis);
    quellen.forEach((src, i) => {
      const fig = document.createElement("figure");
      const rahmen = document.createElement("div");
      rahmen.className = "beleg-gross-bild";
      const img = document.createElement("img");
      img.alt = `Beleg, Seite ${i + 1} von ${quellen.length}`;
      const fertig = () => { img.classList.add("geladen"); rahmen.classList.add("fertig"); };
      img.addEventListener("load", fertig);
      img.addEventListener("error", fertig);
      img.src = src;
      if (img.complete) fertig();  // schon im Browser-Cache -> sofort da
      rahmen.appendChild(img);
      fig.appendChild(rahmen);
      if (quellen.length > 1) {
        const unter = document.createElement("figcaption");
        unter.textContent = `Seite ${i + 1} von ${quellen.length}`;
        fig.appendChild(unter);
      }
      box.appendChild(fig);
    });
    const zu = document.createElement("button");
    zu.className = "beleg-gross-zu";
    zu.type = "button";
    zu.setAttribute("aria-label", "Große Ansicht schließen");
    zu.textContent = "×";
    box.appendChild(zu);
    box.addEventListener("click", schliessen);  // überall klicken schließt (auch aufs Bild)
    document.addEventListener("keydown", taste, true);
    document.body.appendChild(box);
    offen = box;
    zu.focus();
  }

  // Capture-Phase auf document: läuft vor Streamlits eigenem Klick-Handler und kann ihn abfangen
  document.addEventListener("click", (ev) => {
    const huelle = ev.target.closest('[class*="st-key-bildklick_"], [class*="st-key-gross_"]');
    if (!huelle || !ev.target.closest("button")) return;
    const klasse = [...huelle.classList].find((k) => /^st-key-(bildklick|gross)_/.test(k));
    const key = klasse && klasse.replace(/^st-key-(bildklick|gross)_/, "");
    if (!key || key === "schliessen") return;  // Schließ-Fläche im Streamlit-Fenster: nicht anfassen
    const sel = `.st-key-belegbild_${CSS.escape(key)} img, .st-key-belegmehr_${CSS.escape(key)} img`;
    const quellen = [...document.querySelectorAll(sel)].map((img) => img.currentSrc || img.src).filter(Boolean);
    if (!quellen.length) return;  // Rückfall: Streamlit öffnet sein Fenster
    ev.preventDefault();
    ev.stopPropagation();
    ev.stopImmediatePropagation();
    oeffnen(quellen);
  }, true);
})();
