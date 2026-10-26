#!/usr/bin/env node
// 抓真实页面截图当素材：node cdp_shots.mjs <plan.json>
// plan: {"outdir":"...","jobs":[{"url","out","width","height","clip":{"x","y","w","h"},"wait_ms","full_page":false,"cookie":"name=value; ..."}]}
// 只用 Node 内建能力（fetch / WebSocket）+ 本机 Chrome，启动参数必带 --use-mock-keychain，
// 否则 Chrome 会在用户屏幕上弹「找不到钥匙串」。

const [planPath] = process.argv.slice(2);
if (!planPath) {
  console.error("usage: node cdp_shots.mjs <plan.json>");
  process.exit(2);
}
const fs = await import("node:fs/promises");
const path = await import("node:path");
const { spawn } = await import("node:child_process");

const plan = JSON.parse(await fs.readFile(planPath, "utf8"));
const outdir = plan.outdir;
await fs.mkdir(outdir, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const CHROME =
  process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const port = 9000 + Math.floor(Math.random() * 900);
const profile = await fs.mkdtemp("/tmp/cdp-shots-");

const chrome = spawn(
  CHROME,
  [
    "--headless=new",
    "--use-mock-keychain",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-gpu",
    "--hide-scrollbars",
    `--user-data-dir=${profile}`,
    `--remote-debugging-port=${port}`,
    "--window-size=1440,900",
    "about:blank",
  ],
  { stdio: "ignore" },
);

async function httpJson(url, method = "GET") {
  const res = await fetch(url, { method });
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

let ws = null;
let seq = 0;
const pending = new Map();
function send(method, params = {}) {
  const id = ++seq;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}

async function connect() {
  for (let i = 0; i < 120; i++) {
    try {
      const ver = await httpJson(`http://127.0.0.1:${port}/json/version`);
      const t = await httpJson(`http://127.0.0.1:${port}/json/new?about:blank`, "PUT");
      ws = new WebSocket(t.webSocketDebuggerUrl);
      await new Promise((res, rej) => {
        ws.onopen = res;
        ws.onerror = rej;
      });
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.id && pending.has(msg.id)) {
          const { resolve, reject } = pending.get(msg.id);
          pending.delete(msg.id);
          msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
        }
      };
      await send("Page.enable");
      await send("Network.enable");
      return;
    } catch {
      await sleep(150);
    }
  }
  throw new Error("chrome devtools not reachable");
}

const results = [];
try {
  await connect();
  for (const job of plan.jobs || []) {
    const width = job.width || 1440;
    const height = job.height || 900;
    await send("Emulation.setDeviceMetricsOverride", {
      width,
      height,
      deviceScaleFactor: job.scale || 2,
      mobile: false,
    });
    if (job.cookie) {
      const host = new URL(job.url).hostname;
      await send("Network.setCookie", { name: job.cookie.split("=")[0], value: job.cookie.split("=").slice(1).join("="), domain: host, path: "/" });
    }
    if (job.headers) {
      await send("Network.setExtraHTTPHeaders", { headers: job.headers });
    }
    const nav = await send("Page.navigate", { url: job.url });
    if (nav.errorText) throw new Error(`${job.url}: ${nav.errorText}`);
    await sleep(job.wait_ms || 2500);
    let clip = null;
    if (job.full_page) {
      const m = await send("Page.getLayoutMetrics");
      const cs = m.cssContentSize || m.contentSize;
      clip = { x: 0, y: 0, width: Math.min(cs.width, 1600), height: Math.min(cs.height, 4000), scale: 1 };
    } else if (job.clip) {
      clip = { x: job.clip.x, y: job.clip.y, width: job.clip.w, height: job.clip.h, scale: 1 };
    }
    const shot = await send("Page.captureScreenshot", {
      format: "png",
      captureBeyondViewport: !!clip,
      ...(clip ? { clip } : {}),
    });
    const out = path.join(outdir, job.out);
    const buf = Buffer.from(shot.data, "base64");
    await fs.writeFile(out, buf);
    // 小图基本都是错误页/白屏，别让坏素材进片子
    results.push({ url: job.url, out, bytes: buf.length, ok: buf.length > 30000 });
  }
} finally {
  try {
    ws && ws.close();
  } catch {}
  chrome.kill("SIGKILL");
  await fs.rm(profile, { recursive: true, force: true }).catch(() => {});
}
console.log(JSON.stringify(results, null, 2));
const bad = results.filter((r) => !r.ok);
if (bad.length) {
  console.error("suspicious shots: " + bad.map((b) => b.out).join(", "));
  process.exit(1);
}
