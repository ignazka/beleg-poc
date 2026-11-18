// Belegansicht neben dem Formular: durch die Seiten blättern, ohne dass Streamlit die Seite neu berechnet.
// Streamlit zeichnet ALLE Seiten in .st-key-belegbild_<key> (per CSS ist zunächst nur Seite 1 sichtbar),
// darunter die Blätterleiste <div class="belegnav" data-key="<key>"> (‹ Seite x von n ›) und Mini-Vorschauen
// in .st-key-belegdaumen_<key>. Dieses Skript merkt sich je Beleg die gewählte Seite und blendet sie ein.
(function () {
  if (window.__belegAnsicht) return;
  window.__belegAnsicht = true;

  const stand = new Map();  // key -> gewählte Seite (0-basiert)
  const PRAEFIX = "st-key-belegbild_";

  function seitenVon(key) {
    const box = document.querySelector(`.${PRAEFIX}${CSS.escape(key)}`);
    return box ? [...box.children].filter((kind) => kind.querySelector("img")) : [];
  }

  function anwenden(key) {
    const seiten = seitenVon(key);
    if (!seiten.length) return;
    const i = Math.min(Math.max(stand.get(key) || 0, 0), seiten.length - 1);
    stand.set(key, i);
    seiten.forEach((seite, j) => {
      const soll = j === i ? "block" : "none";
      if (seite.style.display !== soll) seite.style.display = soll;
    });
    const nav = [...document.querySelectorAll(".belegnav")].find((n) => n.dataset.key === key);
    if (nav) {
      const text = nav.querySelector("span");
      const neu = `Seite ${i + 1} von ${seiten.length}`;
      if (text && text.textContent !== neu) text.textContent = neu;
      nav.querySelectorAll("button").forEach((b) => {
        const aus = Number(b.dataset.schritt) < 0 ? i === 0 : i === seiten.length - 1;
        if (b.disabled !== aus) b.disabled = aus;
      });
    }
    document.querySelectorAll(`.st-key-belegdaumen_${CSS.escape(key)} img`).forEach((img, j) => {
      const aktiv = j === i ? "1" : "0";
      if (img.dataset.aktiv !== aktiv) img.dataset.aktiv = aktiv;
    });
  }

  function alleAnwenden() {
    document.querySelectorAll(`[class*="${PRAEFIX}"]`).forEach((box) => {
      const klasse = [...box.classList].find((k) => k.startsWith(PRAEFIX));
      if (klasse) anwenden(klasse.slice(PRAEFIX.length));
    });
  }

  function gehe(key, seite) {
    stand.set(key, seite);
    anwenden(key);
  }

  document.addEventListener("click", (ev) => {
    const knopf = ev.target.closest(".belegnav button");
    if (knopf) {
      const key = knopf.closest(".belegnav").dataset.key;
      gehe(key, (stand.get(key) || 0) + Number(knopf.dataset.schritt));
      return;
    }
    const leiste = ev.target.closest('[class*="st-key-belegdaumen_"]');
    if (leiste && ev.target.tagName === "IMG") {
      const klasse = [...leiste.classList].find((k) => k.startsWith("st-key-belegdaumen_"));
      const bilder = [...leiste.querySelectorAll("img")];
      gehe(klasse.slice("st-key-belegdaumen_".length), bilder.indexOf(ev.target));
    }
  });

  // Streamlit zeichnet bei Eingaben neu – danach die gewählte Seite wieder einblenden (gebündelt pro Frame)
  let geplant = false;
  new MutationObserver(() => {
    if (geplant) return;
    geplant = true;
    requestAnimationFrame(() => { geplant = false; alleAnwenden(); });
  }).observe(document.body, { childList: true, subtree: true });
  alleAnwenden();
})();
