// Manueller Browser-Test der großen Belegansicht (assets/belegansicht.js); braucht Chrome + Node 22+.
// App lokal starten (LOGIN_DEAKTIVIERT=1, 127.0.0.1:8503), dann:
//   node tests/browser/belegansicht.mjs http://127.0.0.1:8503 [screenshot.png]
// Öffnet im Tab „Belege“ die große Ansicht per Klick aufs Bild, per Knopf und schließt per Klick und Esc.
// Meldet die Zeit bis zur Anzeige (ohne Neuberechnung durch Streamlit: wenige Millisekunden).
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
  // Tour nicht automatisch starten lassen
  await cdp("Page.addScriptToEvaluateOnNewDocument", { source: "localStorage.setItem('praktikantai_tour_gesehen', '1')" });
  await cdp("Page.navigate", { url });
  for (let i = 0; i < 120 && (await js("document.querySelectorAll('[role=tab]').length")) < 4; i++) await warte(500);
  // Tab „Belege“ per echtem Mausklick öffnen
  const pos = await js("(() => { const r = document.querySelectorAll('[role=tab]')[1].getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2}; })()");
  for (const type of ["mouseMoved", "mousePressed", "mouseReleased"]) await cdp("Input.dispatchMouseEvent", { type, x: pos.x, y: pos.y, button: "left", clickCount: 1 });
  for (let i = 0; i < 60 && !(await js("!!document.querySelector('.st-key-belegspalte_belege [class*=st-key-bildklick_] button')")); i++) await warte(500);
  await warte(1500);

  const klickUndMessen = (selektor) => js(`(async () => {
    const b = document.querySelector(${JSON.stringify(selektor)}); if (!b) return {fehlt: true};
    const t0 = performance.now(); b.click();
    for (let i = 0; i < 100 && !document.querySelector('.beleg-gross'); i++) await new Promise(r => setTimeout(r, 10));
    const box = document.querySelector('.beleg-gross');
    await new Promise(r => setTimeout(r, 400));
    return {offen: !!box, ms: Math.round(performance.now() - t0) - 400 >= 0 ? null : null, bisAnzeige: box ? Math.round(box.__t || 0) : null,
            seiten: box ? box.querySelectorAll('img').length : 0, geladen: box ? box.querySelectorAll('img.geladen').length : 0,
            streamlitDialog: !!document.querySelector('[data-testid=stDialog]'), fokus: document.activeElement?.className};
  })()`);
  const zeit = (selektor) => js(`(async () => {
    const b = document.querySelector(${JSON.stringify(selektor)}); const t0 = performance.now(); b.click();
    while (!document.querySelector('.beleg-gross') && performance.now() - t0 < 5000) await new Promise(r => setTimeout(r, 2));
    return Math.round(performance.now() - t0);
  })()`);

  const erg = {};
  erg.bild_ms = await zeit(".st-key-belegspalte_belege [class*=st-key-bildklick_] button");
  await warte(400);
  erg.bild = await js("({seiten: document.querySelectorAll('.beleg-gross img').length, geladen: document.querySelectorAll('.beleg-gross img.geladen').length, streamlitDialog: !!document.querySelector('[data-testid=stDialog]'), fokus: document.activeElement?.className})");
  if (bildDatei) {
    const bild = await cdp("Page.captureScreenshot", { format: "png" });
    writeFileSync(bildDatei, Buffer.from(bild.result.data, "base64"));
  }
  await js("document.querySelector('.beleg-gross img').click()");
  await warte(200);
  erg.nachKlickZu = !(await js("!!document.querySelector('.beleg-gross')"));
  erg.knopf_ms = await zeit(".st-key-belegspalte_belege [class*=st-key-gross_] button");
  await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await warte(200);
  erg.nachEscZu = !(await js("!!document.querySelector('.beleg-gross')"));
  await warte(1500);
  erg.keinStreamlitDialog = !(await js("!!document.querySelector('[data-testid=stDialog]')"));
  console.log(JSON.stringify({ erg, fehler }, null, 1));
  ws.close();
} finally { chrome.kill(); }
