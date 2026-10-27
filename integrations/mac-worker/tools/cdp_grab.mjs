#!/usr/bin/env node
// 素材抓取引擎：node cdp_grab.mjs <plan.json>
//
// 为什么单独一个文件、而不是直接改 paper/cdp_shots.mjs：
//   cdp_shots.mjs 是纸面引擎在用的稳定生产路径（固定 1440 宽、一次一张、失败只按文件大小猜）。
//   素材抓取要多三件事——竖屏 viewport、滚动分段、按选择器等待，还要能把失败说清楚
//   （要登录 / 超时 / 空页）。改老文件会把回归风险带进出片链路，所以复制连接骨架、独立演进。
//
// 只用 Node 内建能力（fetch / WebSocket）+ 本机 Chrome，不引任何依赖。
// Chrome 启动参数必带 --use-mock-keychain：不带会反复弹「找不到钥匙串」，弹窗在用户屏幕上。
//
// plan.json:
//   { "outdir": "/abs/dir", "viewport": {"width":1080,"height":1920}, "scale": 1,
//     "wait_ms": 2500, "timeout_ms": 20000, "scroll": 0, "wait_for": null,
//     "user_agent": null, "jobs": [ {"url":"https://...", "name":"prefix", "n":1} ] }
// 每张图输出一行 JSON（stdout），全部失败信息也在这里；有失败时 exit 1。

const [planPath] = process.argv.slice(2);
if (!planPath) {
  console.error("usage: node cdp_grab.mjs <plan.json>");
  process.exit(2);
}

const fs = await import("node:fs/promises");
const path = await import("node:path");
const { spawn } = await import("node:child_process");

const plan = JSON.parse(await fs.readFile(planPath, "utf8"));
const outdir = plan.outdir;
if (!outdir) {
  console.error("plan.outdir 必填");
  process.exit(2);
}
await fs.mkdir(outdir, { recursive: true });

const VIEW = plan.viewport || { width: 1080, height: 1920 };
const WAIT_MS = plan.wait_ms ?? 2500;
const TIMEOUT_MS = plan.timeout_ms ?? 20000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const CHROME =
  process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const port = 9100 + Math.floor(Math.random() * 800);
const profile = await fs.mkdtemp("/tmp/cdp-grab-");

const chrome = spawn(
  CHROME,
  [
    "--headless=new",
    "--use-mock-keychain",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-gpu",
    "--hide-scrollbars",
    "--mute-audio",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    `--user-data-dir=${profile}`,
    `--remote-debugging-port=${port}`,
    `--window-size=${VIEW.width},${VIEW.height}`,
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
let mainStatus = null; // 主文档 HTTP 状态码，用来区分 404/500 与空页

function send(method, params = {}) {
  const id = ++seq;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}

async function connect() {
  for (let i = 0; i < 160; i++) {
    try {
      await httpJson(`http://127.0.0.1:${port}/json/version`);
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
          return;
        }
        // 事件：只关心主文档响应码，别的忽略（不要在这里堆逻辑）
        if (msg.method === "Network.responseReceived") {
          const r = msg.params && msg.params.response;
          if (r && msg.params.type === "Document" && !mainStatus) {
            mainStatus = { status: r.status, mime: r.mimeType };
          }
        }
      };
      await send("Page.enable");
      await send("Network.enable");
      await send("Runtime.enable");
      return;
    } catch {
      await sleep(150);
    }
  }
  throw new Error("chrome devtools not reachable");
}

const evalJs = async (expression) => {
  const r = await send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (r.exceptionDetails) {
    throw new Error(`js error: ${r.exceptionDetails.text || "exception"}`);
  }
  return r.result ? r.result.value : undefined;
};

// 等选择器出现；超时返回 false，不抛，交给调用方给「timeout」这个明确原因
async function waitForSelector(sel, timeoutMs) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const ok = await evalJs(
        `(() => { try { return !!document.querySelector(${JSON.stringify(sel)}); } catch (e) { return false; } })()`,
      );
      if (ok) return true;
    } catch {
      // 导航中/上下文销毁，继续等
    }
    await sleep(200);
  }
  return false;
}

function slugName(u, i) {
  try {
    const x = new URL(u);
    return (x.hostname.replace(/^www\./, "") + x.pathname).replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").slice(0, 40) || `page-${i}`;
  } catch {
    return `page-${i}`;
  }
}

const results = [];
try {
  await connect();
  let idx = 0;
  for (const job of plan.jobs || []) {
    idx += 1;
    mainStatus = null;
    const url = job.url;
    const n = Math.max(1, Math.min(20, job.n ?? plan.n ?? 1));
    const nameBase = job.name || slugName(url, idx);
    const waitFor = job.wait_for ?? plan.wait_for ?? null;
    const waitMs = job.wait_ms ?? WAIT_MS;
    const timeoutMs = job.timeout_ms ?? TIMEOUT_MS;
    const scrollPx = job.scroll ?? plan.scroll ?? 0;
    const base = { url, name_base: nameBase };

    try {
      await send("Emulation.setDeviceMetricsOverride", {
        width: VIEW.width,
        height: VIEW.height,
        deviceScaleFactor: plan.scale || 1,
        mobile: !!plan.mobile,
      });
      if (plan.user_agent) {
        await send("Network.setUserAgentOverride", { userAgent: plan.user_agent });
      }
      if (job.cookie || plan.cookie) {
        const c = job.cookie || plan.cookie;
        const host = new URL(url).hostname;
        const eq = c.indexOf("=");
        await send("Network.setCookie", {
          name: c.slice(0, eq),
          value: c.slice(eq + 1),
          domain: host,
          path: "/",
        });
      }

      // 导航：load 事件 + errorText 双判，避免把错误页当成功
      const nav = await send("Page.navigate", { url });
      if (nav.errorText) {
        results.push({ ...base, ok: false, reason: "nav_error", detail: nav.errorText });
        continue;
      }
      await sleep(waitMs);

      if (waitFor) {
        const got = await waitForSelector(waitFor, timeoutMs);
        if (!got) {
          results.push({
            ...base, ok: false, reason: "timeout",
            detail: `等了 ${timeoutMs}ms 没出现选择器 ${waitFor}`,
          });
          continue;
        }
      }

      const info = await evalJs(`(() => {
        const t = document.title || "";
        const txt = (document.body ? document.body.innerText : "") || "";
        return {
          title: t,
          text_len: txt.trim().length,
          html_len: document.documentElement ? document.documentElement.innerHTML.length : 0,
          has_pwd: !!document.querySelector('input[type=password]'),
          url: location.href,
          scroll_h: Math.max(document.body ? document.body.scrollHeight : 0, document.documentElement ? document.documentElement.scrollHeight : 0),
        };
      })()`);

      const httpStatus = mainStatus ? mainStatus.status : null;
      // 失败三分法：先看要不要登录，再看是不是错误页，最后看是不是空壳
      if (httpStatus && httpStatus >= 400) {
        results.push({
          ...base, ok: false, reason: "http_error",
          detail: `HTTP ${httpStatus}`, final_url: info.url,
        });
        continue;
      }
      // 有密码框就是登录墙：GitHub 登录页正文也有几百字（页脚/导航），不能只看「字少」
      if (info.has_pwd && info.text_len < 800) {
        results.push({
          ...base, ok: false, reason: "login_required",
          detail: "页面是密码输入框（未登录）", final_url: info.url, title: info.title,
        });
        continue;
      }
      if (info.text_len < 15 && info.html_len < 800) {
        results.push({
          ...base, ok: false, reason: "empty",
          detail: `正文 ${info.text_len} 字 / DOM ${info.html_len} 字节，疑似空页或还没渲染`,
          final_url: info.url, title: info.title,
        });
        continue;
      }

      for (let k = 0; k < n; k++) {
        // 多张时按视口高度逐段下移；单张且给了 --scroll 就先滚到位再拍
        const y = n > 1 ? Math.round(k * VIEW.height * 0.9) : Math.round(k * scrollPx);
        if (y > 0) {
          await evalJs(`window.scrollTo(0, ${y}); true`);
          await sleep(350);
        }
        const shot = await send("Page.captureScreenshot", {
          format: "png",
          captureBeyondViewport: false,
        });
        const buf = Buffer.from(shot.data, "base64");
        const suffix = n > 1 ? `-${k + 1}` : scrollPx ? `-scroll` : "";
        const fileName = `${nameBase}${suffix}.png`;
        const out = path.join(outdir, fileName);
        await fs.writeFile(out, buf);
        // 小图基本都是白屏/纯色，单独标出来，别让坏素材进片子
        const tiny = buf.length < 12000;
        results.push({
          ...base,
          ok: !tiny,
          reason: tiny ? "empty" : "",
          detail: tiny ? `截图只有 ${buf.length} 字节，疑似白屏` : "",
          file: fileName,
          bytes: buf.length,
          scroll_y: y,
          final_url: info.url,
          title: info.title,
          page_height: info.scroll_h,
          http_status: httpStatus,
        });
      }
    } catch (e) {
      results.push({ ...base, ok: false, reason: "exception", detail: String(e && e.message ? e.message : e) });
    }
  }
} finally {
  try {
    ws && ws.close();
  } catch {}
  try {
    chrome.kill("SIGKILL");
  } catch {}
  await fs.rm(profile, { recursive: true, force: true }).catch(() => {});
}

for (const r of results) console.log(JSON.stringify(r));
const bad = results.filter((r) => !r.ok);
if (bad.length) process.exit(1);
