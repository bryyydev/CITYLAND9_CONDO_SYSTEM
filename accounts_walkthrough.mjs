// Read-only walkthrough of every account on the LIVE server (127.0.0.1:5000).
// Usage (server running, demo accounts present):  node tests/e2e/accounts_walkthrough.mjs
// Writes test-output/live/account-test-report.html (screenshots + PASS/FAIL) and opens it.
// Signs in, checks portal + menu, screenshots the dashboards, tries one forbidden page.
// Nothing is submitted; only Login/Logout audit entries are written.
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const SP = process.argv[2] || join(process.cwd(), "test-output");
const BASE = "http://127.0.0.1:5000";
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const OUT = join(SP, "live"); mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--no-first-run", "--remote-debugging-port=9335",
  `--user-data-dir=${join(SP, "edge-live")}`, "about:blank"], { stdio: "ignore" });
let target;
for (let i = 0; i < 40 && !target; i++) { try { target = (await (await fetch("http://127.0.0.1:9335/json/list")).json()).find((t) => t.type === "page"); } catch {} if (!target) await sleep(250); }
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r));
let seq = 0; const pending = new Map();
ws.addEventListener("message", (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
const cdp = (method, params = {}) => new Promise((resolve) => { const id = ++seq; pending.set(id, resolve); ws.send(JSON.stringify({ id, method, params })); });
const js = async (e) => (await cdp("Runtime.evaluate", { expression: e, awaitPromise: true, returnByValue: true })).result?.result?.value;
async function waitFor(e, t = 8000) { for (let i = 0; i < t; i += 150) { if (await js(e)) return true; await sleep(150); } return false; }
async function go(p) { await cdp("Page.navigate", { url: BASE + p }); await sleep(500); await waitFor("document.readyState === 'complete'"); }
async function shot(name) { await sleep(500); const { result } = await cdp("Page.captureScreenshot", { format: "png" }); const f = join(OUT, name + ".png"); writeFileSync(f, Buffer.from(result.data, "base64")); return f; }
const type = (sel, v) => js(`(() => { const el = document.querySelector(${JSON.stringify(sel)}); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, ${JSON.stringify(v)}); el.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`);
const clickText = (sel, text) => js(`(() => { const el = [...document.querySelectorAll(${JSON.stringify(sel)})].find(e => e.textContent.trim().includes(${JSON.stringify(text)})); if (!el) return false; el.click(); return true; })()`);
const navText = () => js("[...document.querySelectorAll('.nav a')].map(a => a.textContent.trim())");

const ACCOUNTS = [
  { user: "superadmin", pw: "admin123", label: "Superadmin", portal: "superadmin", legacy: ["/dashboard", "Legacy dashboard"], extra: ["/users", "Users & Access"], forbidden: null,
    mustSee: ["Dashboard", "Billing Management", "Payroll", "Users & Access", "Audit Logs", "Rates & Rules"], mustNot: [] },
  { user: "demo_admin", pw: "Demo@1234", label: "Admin", portal: "admin", legacy: ["/dashboard", "Legacy dashboard"], extra: ["/billing", "Billing Management"], forbidden: "/settings",
    mustSee: ["Dashboard", "Unit Directory", "Billing Management", "Water Readings", "Condo Reports"], mustNot: ["Users & Access", "Audit Logs", "Rates & Rules", "Payroll"] },
  { user: "demo_manager", pw: "Demo@1234", label: "Manager (HR & Payroll)", portal: "hr", legacy: ["/employees", "Employees"], extra: ["/employees/payroll", "Payroll"], forbidden: "/billing",
    mustSee: ["Employees", "Attendance", "Payroll", "Payroll Rules", "Announcements"], mustNot: ["Billing Management", "Condo Reports", "Dashboard"] },
  { user: "demo_staff", pw: "Demo@1234", label: "Staff", portal: "staff", legacy: ["/move-certificate", "Move In / Out"], extra: ["/maintenance", "Maintenance"], forbidden: "/reports",
    mustSee: ["Move In / Out", "Gate Pass", "Expenses", "Attendance", "Maintenance"], mustNot: ["Condo Reports", "Audit Logs", "Billing Management"] },
  { user: "demo_accounting", pw: "Demo@1234", label: "Accounting", portal: "accounting", legacy: ["/billing", "Billing Management"], extra: ["/reports", "Condo Reports"], forbidden: "/units",
    mustSee: ["Billing Management", "Advance Payments", "Condo Reports", "Audit Logs"], mustNot: ["Unit Directory", "SOA Email", "Payroll"] },
  { user: "demo_resident", pw: "Demo@1234", label: "Resident", portal: "resident", legacy: null, extra: null, forbidden: "/billing",
    mustSee: ["Home", "Statement of Account", "Maintenance Requests", "Notices"], mustNot: ["Billing Management", "Unit Directory"] },
];

const report = [];
try {
  await cdp("Page.enable"); await cdp("Runtime.enable"); await cdp("Network.enable");
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  for (const a of ACCOUNTS) {
    const r = { ...a, checks: [], shots: [] };
    const check = (name, ok, detail = "") => { r.checks.push({ name, ok, detail }); console.log(`${ok ? "PASS" : "FAIL"}  [${a.user}] ${name}${detail ? " — " + detail : ""}`); };
    await cdp("Network.clearBrowserCookies");
    await go("/app/login"); await waitFor("!!document.querySelector('#username')");
    await type("#username", a.user); await type("#password", a.pw);
    await js("document.querySelector('button.btn.primary').click()");
    check(`signs in and lands on /app/${a.portal}`, await waitFor(`location.pathname === '/app/${a.portal}'`));
    const nav = await navText();
    const missing = a.mustSee.filter((x) => !nav.includes(x)), extra = a.mustNot.filter((x) => nav.includes(x));
    check("menu shows the right modules", !missing.length && !extra.length, missing.length ? `missing ${missing}` : extra.length ? `should not show ${extra}` : `${nav.length} links`);
    r.nav = nav;
    r.shots.push(["Portal home", await shot(`${a.user}-1-home`)]);

    if (a.portal === "resident") {
      check("balance and unit shown", await waitFor("/₱[\\d,]+\\.\\d\\d/.test(document.querySelector('.kpi .value')?.textContent || '') && document.body.innerText.includes('TEST-502')"));
      await clickText(".nav a", "Statement of Account");
      check("statement list loads", await waitFor("!!document.querySelector('tbody tr')"));
      await js("document.querySelector('tbody tr a.btn').click()");
      check("SOA detail opens", await waitFor("document.body.innerText.includes('TOTAL AMOUNT DUE')"));
      r.shots.push(["Statement of Account", await shot(`${a.user}-2-soa`)]);
      await clickText(".nav a", "Maintenance");
      check("maintenance page loads", await waitFor("!!document.querySelector('#m-title')"));
      r.shots.push(["Maintenance Requests", await shot(`${a.user}-3-maintenance`)]);
      const other = await js("fetch('/api/resident/units/1/summary').then(r => r.status)");
      check("cannot read another unit's data (API)", other === 403, `status ${other}`);
    } else {
      await go(a.legacy[0]);
      check(`opens ${a.legacy[1]} (current system)`, await waitFor(`location.pathname === '${a.legacy[0]}' && !document.body.innerText.includes('You do not have permission')`));
      r.shots.push([`${a.legacy[1]} (current system)`, await shot(`${a.user}-2-legacy`)]);
      await go(a.extra[0]);
      check(`opens ${a.extra[1]}`, await waitFor(`location.pathname === '${a.extra[0]}' && !document.body.innerText.includes('You do not have permission')`));
      r.shots.push([a.extra[1], await shot(`${a.user}-3-extra`)]);
    }
    if (a.forbidden) {
      await go(a.forbidden);
      check(`typing ${a.forbidden} is refused by the server`, await waitFor(`location.pathname !== '${a.forbidden}'`), `sent to ${await js("location.pathname")}`);
      await go(`/app/${a.portal === "admin" ? "superadmin" : "admin"}`);
      check("another role's portal shows 'no access'", await waitFor("document.body.innerText.includes(\"You don't have access to this page\")"));
    }
    report.push(r);
  }
} catch (err) { console.log("SCRIPT ERROR", err.stack); }
finally {
  ws.close(); edge.kill();
  const all = report.flatMap((r) => r.checks), failed = all.filter((c) => !c.ok).length;
  const img = (f) => `data:image/png;base64,${readFileSync(f).toString("base64")}`;
  const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CityLand 9 — Account Test Report</title>
<style>body{font-family:"Segoe UI",system-ui,sans-serif;margin:0;background:#f1f6f8;color:#18384d}header{background:#2f7888;color:#fff;padding:24px 32px}h1{margin:0;font-size:24px}
main{padding:24px 32px;max-width:1400px}.sum{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}.pill{background:#fff;border:1px solid #d2e1e6;border-radius:10px;padding:10px 14px}
section{background:#fff;border:1px solid #d2e1e6;border-radius:12px;padding:18px 22px;margin:18px 0}h2{margin:0 0 4px}.muted{color:#55748a}
table{border-collapse:collapse;width:100%;margin:10px 0}td{padding:6px 8px;border-bottom:1px solid #e3edf1;vertical-align:top}.ok{color:#1f7a4d;font-weight:700}.bad{color:#b3261e;font-weight:700}
.shots{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:14px}figure{margin:0}figure img{width:100%;border:1px solid #d2e1e6;border-radius:8px}figcaption{font-size:13px;color:#55748a;margin-top:4px}
.nav{font-size:13px;color:#55748a}</style></head><body>
<header><h1>CityLand 9 — Account & Dashboard Test</h1><div>Live system http://127.0.0.1:5000 · ${new Date().toLocaleString("en-PH")} · read-only walkthrough</div></header><main>
<div class="sum"><div class="pill"><b>${all.length - failed}/${all.length}</b> checks passed</div><div class="pill"><b>${report.length}</b> accounts tested</div></div>
${report.map((r) => `<section><h2>${esc(r.label)} <span class="muted">— ${esc(r.user)} → /app/${esc(r.portal)}</span></h2>
<p class="nav"><b>Menu:</b> ${r.nav.map(esc).join(" · ")}</p>
<table>${r.checks.map((c) => `<tr><td class="${c.ok ? "ok" : "bad"}">${c.ok ? "PASS" : "FAIL"}</td><td>${esc(c.name)}</td><td class="muted">${esc(c.detail)}</td></tr>`).join("")}</table>
<div class="shots">${r.shots.map(([cap, f]) => `<figure><img src="${img(f)}" alt="${esc(r.label)}: ${esc(cap)}"><figcaption>${esc(cap)}</figcaption></figure>`).join("")}</div></section>`).join("")}
</main></body></html>`;
  writeFileSync(join(OUT, "account-test-report.html"), html);
  console.log(`\n${all.length - failed}/${all.length} checks passed — report: ${join(OUT, "account-test-report.html")}`);
  if (!process.argv[2]) spawn("cmd", ["/c", "start", "", join(OUT, "account-test-report.html")], { detached: true, stdio: "ignore" });
  process.exit(failed ? 1 : 0);
}
