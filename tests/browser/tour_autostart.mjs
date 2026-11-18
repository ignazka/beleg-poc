// Manueller Browser-Test: Autostart der Einführung (frischer Browser -> Tour; Neuladen -> keine; Knopf -> Tour).
// App wie in tour_durchklicken.mjs starten, dann:  node tests/browser/tour_autostart.mjs http://127.0.0.1:8503
import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [url] = process.argv.slice(2);
const PORT = 9226;
const chrome = spawn(process.env.CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe", [
  "--headless=new", "--disable-gpu", "--no-first-run", `--user-data-dir=${mkdtempSync(join(tmpdir(), "chrome-auto-"))}`,
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
  const popoverNach = async (sek) => {
    for (let i = 0; i < sek * 2; i++) { if (await js("!!document.querySelector('.driver-popover')")) return true; await warte(500); }
    return false;
  };
  const erg = {};
  await cdp("Page.navigate", { url });
  erg.ersterBesuch_tourStartetAllein = await popoverNach(120);
  erg.titel = await js("document.querySelector('.driver-popover-title')?.innerText");
  erg.merker = !!(await js("localStorage.getItem('praktikantai_tour_gesehen')"));
  await cdp("Page.reload");
  await warte(3000);
  for (let i = 0; i < 60 && !(await js("!!document.querySelector('.st-key-einfuehrung_start button')")); i++) await warte(500);
  erg.neuladen_keineTour = !(await popoverNach(8));
  await js("document.querySelector('.st-key-einfuehrung_start button').click()");
  erg.knopf_startetTour = await popoverNach(20);
  console.log(JSON.stringify({ erg, fehler }, null, 1));
  ws.close();
} finally { chrome.kill(); }
