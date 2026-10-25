#!/usr/bin/env node
// 把一个 HTML 页面按固定帧率逐帧截成 JPEG 序列。
// 用法：node cdp_frames.mjs <port> <file-url> <fps> <seconds> <outdir>
// 只依赖 Node 内建能力（Node 24 自带 WebSocket），不需要装 playwright。

const [port, fileUrl, fpsArg, secArg, outDir] = process.argv.slice(2);
const fps = Number(fpsArg || 30);
const seconds = Number(secArg || 5);
const width = 1080;
const height = 1920;
const fs = await import("node:fs/promises");
const path = await import("node:path");
const crypto = await import("node:crypto");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function httpJson(url, method = "GET") {
  const res = await fetch(url, { method });
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

async function main() {
  let ver = null;
  for (let i = 0; i < 100; i++) {
    try {
      ver = await httpJson(`http://127.0.0.1:${port}/json/version`);
      break;
    } catch {
      await sleep(100);
    }
  }
  if (!ver) throw new Error("chrome devtools not reachable");
  const target = await httpJson(
    `http://127.0.0.1:${port}/json/new?${encodeURIComponent("about:blank")}`,
    "PUT",
  );
  const wsUrl = target.webSocketDebuggerUrl;
  const ws = new WebSocket(wsUrl);
  let seq = 0;
  const pending = new Map();
  const events = [];
  ws.addEventListener("message", (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(JSON.stringify(msg.error)));
      else resolve(msg.result);
    } else if (msg.method) {
      events.push(msg.method);
    }
  });
  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve, { once: true });
    ws.addEventListener("error", reject, { once: true });
  });
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = ++seq;
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params }));
    });

  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", {
    width,
    height,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await send("Page.navigate", { url: fileUrl });
  for (let i = 0; i < 200; i++) {
    const r = await send("Runtime.evaluate", {
      expression: "document.readyState",
      returnByValue: true,
    });
    if (r.result && r.result.value === "complete") break;
    await sleep(50);
  }
  // 等字体真正排好：截图早于字体加载会得到错误的断行
  await send("Runtime.evaluate", { expression: "document.fonts.ready", awaitPromise: true });
  await send("Runtime.evaluate", { expression: "window.renderFrame && window.renderFrame(0)" });
  await sleep(120);

  const total = Math.round(fps * seconds);
  await fs.mkdir(outDir, { recursive: true });
  for (let i = 0; i < total; i++) {
    await send("Runtime.evaluate", {
      expression: `window.renderFrame(${(i / fps).toFixed(5)})`,
    });
    const shot = await send("Page.captureScreenshot", {
      format: "jpeg",
      quality: 94,
      captureBeyondViewport: false,
      optimizeForSpeed: true,
    });
    const name = `f${String(i).padStart(5, "0")}.jpg`;
    await fs.writeFile(path.join(outDir, name), Buffer.from(shot.data, "base64"));
  }
  console.log(JSON.stringify({ frames: total, outDir, token: crypto.randomUUID().slice(0, 8) }));
  ws.close();
}

await main();
