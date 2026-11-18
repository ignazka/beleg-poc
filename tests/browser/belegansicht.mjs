// Manueller Browser-Test der Belegansicht im Tab „Belege“ (assets/belegansicht.js); braucht Chrome + Node 22+.
// App lokal starten (LOGIN_DEAKTIVIERT=1, 127.0.0.1:8503), dann:
//   node tests/browser/belegansicht.mjs http://127.0.0.1:8503 [screenshot.png]
// Prüft: Seite so hoch wie die rechte Spalte, ‹ › blättern (sofort, ohne Neuberechnung), Mini-Vorschau springt,
// gewählte Seite bleibt nach einer Eingabe (Streamlit rechnet neu) erhalten. Erster Beleg sollte mehrseitig sein.
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [url, bildDatei] = process.argv.slice(2);
const PORT = 9227;
const chrome = spawn(process.env.CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe", [
  "--headless=new", "--disable-gpu", "--no-first-run", `--user-data-dir=${mkdtempSync(join(tmpdir(), "chrome-ansicht-"))}`,
  `--remote-debugging-port=${PORT}`, "--window-size=1280,900", "about:blank"], { stdio: "ignore" });
const warte = (ms) => new Promise((r) => setTimeout(r, ms));
try {
  let ziel;
  for (let i = 0; i < 50 && !ziel; i++) {
    try { ziel = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find((t) => t.type === "page"); }
    catch { await warte(200); }
  }
  const ws = new WebSocket(ziel.webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener("open", r, { once: true }));
  let id = 0; const offen = new Map(); const fehler = [];
  ws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.method === "Runtime.exceptionThrown") fehler.push((m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text).slice(0, 160));
    if (m.id && offen.has(m.id)) { offen.get(m.id)(m); offen.delete(m.id); }
  });
  const cdp = (method, params = {}) => new Promise((r) => { const n = ++id; offen.set(n, r); ws.send(JSON.stringify({ id: n, method, params })); });
  const js = async (expr) => (await cdp("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;
  await cdp("Runtime.enable");
  await cdp("Page.enable");
  await cdp("Page.addScriptToEvaluateOnNewDocument", { source: "try { localStorage.setItem('praktikantai_tour_gesehen', '1') } catch (e) {}" });
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
  await cdp("Page.navigate", { url });
  for (let i = 0; i < 120 && (await js("document.querySelectorAll('[role=tab]').length")) < 4; i++) await warte(500);
  const pos = await js("(() => { const r = document.querySelectorAll('[role=tab]')[1].getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2}; })()");
  for (const type of ["mouseMoved", "mousePressed", "mouseReleased"]) await cdp("Input.dispatchMouseEvent", { type, x: pos.x, y: pos.y, button: "left", clickCount: 1 });
  for (let i = 0; i < 60 && !(await js("!!document.querySelector('.st-key-belegspalte_belege .belegnav')")); i++) await warte(500);
  for (let i = 0; i < 40 && !(await js("!!window.__belegAnsicht")); i++) await warte(250);
  await warte(1500);

  const zustand = `(() => {
    const spalte = document.querySelector('.st-key-belegspalte_belege');
    const rechts = spalte.closest('[data-testid=stColumn]').nextElementSibling;
    const box = spalte.querySelector('[class*=st-key-belegbild_]');
    const seiten = [...box.children].filter(k => k.querySelector('img'));
    const sichtbar = seiten.findIndex(s => getComputedStyle(s).display !== 'none');
    const img = seiten[sichtbar]?.querySelector('img');
    const nav = spalte.querySelector('.belegnav');
    return {seiten: seiten.length, sichtbar, anzahlSichtbar: seiten.filter(s => getComputedStyle(s).display !== 'none').length,
      bildHoehe: Math.round(img.getBoundingClientRect().height), spalteHoehe: Math.round(spalte.getBoundingClientRect().height),
      rechtsHoehe: Math.round(rechts.getBoundingClientRect().height), text: nav.querySelector('span').textContent,
      zurueckAus: nav.querySelectorAll('button')[0].disabled, vorAus: nav.querySelectorAll('button')[1].disabled,
      daumen: [...spalte.querySelectorAll('[class*=st-key-belegdaumen_] img')].map(i => i.dataset.aktiv)};
  })()`;
  const erg = { start: await js(zustand) };
  if (bildDatei) writeFileSync(bildDatei, Buffer.from((await cdp("Page.captureScreenshot", { format: "png" })).result.data, "base64"));
  erg.vor_ms = await js(`(async () => { const t0 = performance.now();
    document.querySelector('.st-key-belegspalte_belege .belegnav button[data-schritt="1"]').click();
    return Math.round(performance.now() - t0); })()`);
  await warte(300);
  erg.nachVor = await js(zustand);
  if (bildDatei) writeFileSync(bildDatei.replace(/\.png$/, "_seite2.png"), Buffer.from((await cdp("Page.captureScreenshot", { format: "png" })).result.data, "base64"));
  // Eingabe -> Streamlit rechnet neu; die gewählte Seite muss bleiben
  await js("(() => { const r = [...document.querySelectorAll('label')].find(l => l.innerText.includes('Ja, stimmt')); r && r.click(); })()");
  await warte(3000);
  erg.nachNeuberechnung = await js(zustand);
  await js("document.querySelectorAll('.st-key-belegspalte_belege [class*=st-key-belegdaumen_] img')[0].click()");
  await warte(300);
  erg.nachDaumen = await js(zustand);
  console.log(JSON.stringify({ erg, fehler }, null, 1));
  ws.close();
} finally { chrome.kill(); }
