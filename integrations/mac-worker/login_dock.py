#!/usr/bin/env python3
"""扫码登录坞（正式版）：一个页面接入所有平台，扫码即登录。

设计要点：
  * 平台清单**不硬编码**，从 platform_registry.py 自动发现 SAU 支持的平台，
    上游加平台，这里自动多一张卡片；
  * 按平台的 loginMode 给不同操作：
      qr           点一下就出二维码，扫完自动落 cookie
      qr_terminal  必须在本地终端跑（bilibili 硬限制），页面给出可复制命令
      browser      可在面板拉起，但需人在浏览器里操作
      cookie       只能导入 cookie，页面给出文件位置
  * 一次只允许一个登录流程：SAU 各平台都要独占可见浏览器窗口，并发会集体失败；
  * 只读 cookie 目录判断「已登录」，绝不发任何内容。

接口：
  GET  /                 页面
  GET  /api/state        每个平台的状态 + 是否已登录
  GET  /api/platforms    完整注册表
  POST /api/start/<plat> 发起登录
  GET  /qr/<plat>.png    二维码图片
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import platform_registry

BASE = Path(os.path.expanduser(
    "~/Desktop/work/Studio/creator-studio/integrations/vendor/social-auto-upload"))
PY = BASE / ".venv/bin/python"
COOKIES = BASE / "cookies"
OUT = Path(os.path.expanduser("~/CreatorStudio/deliver/qr"))
ACCOUNT = "yefen"

# launchd 下读不到 ~/Desktop 的目录项（listdir 报 Operation not permitted，
# 但对已知路径 stat 可以）。所以「枚举 cookie/二维码文件」必须走镜像目录，
# 由 sync_state.sh 定期 rsync 到 ~ 卷。写盘仍然写仓库目录（SAU 自己要写）。
COOKIES_MIRROR = Path(os.path.expanduser("~/CreatorStudio/state/sau/cookies"))
SCRATCH = Path(os.path.expanduser("~/.hermes/cache/scratch"))
# SAU 把二维码额外导出到这里（~ 卷），登录坞从这里读 —— 绕开 Desktop 的 TCC 限制。
QR_EXPORT = Path(os.path.expanduser("~/CreatorStudio/state/qr"))

# cookie 有效的最小体积。B站实测只有 1360B 却完全可用，所以阈值不能定太高；
# 300B 足以排除「空文件 / 只有 meta 的残骸」。
MIN_COOKIE_BYTES = 300

_lock = threading.Lock()
_running: dict[str, subprocess.Popen] = {}
_status: dict[str, dict] = {}
# 每个平台各自记录本次登录的启动时间。不能用单一全局值：并发/连续登录时
# 后一次会覆盖前一次，导致前一个平台的 cookie 判定窗口错位。
_started_at: dict[str, float] = {}


# ── cookie 判定 ────────────────────────────────────────────────────────────

def cookie_path(plat: str) -> Path:
    """cookie 的实际落盘位置（SAU 写这里）。"""
    return COOKIES / f"{plat}_{ACCOUNT}.json"


def _dirs() -> list[Path]:
    """可枚举的 cookie 目录，镜像优先。

    launchd 下 listdir(~/Desktop/...) 会 PermissionError，所以镜像目录排第一；
    交互式终端下两者都在，镜像由 sync_state.sh 保新鲜。
    """
    return [COOKIES_MIRROR, COOKIES]


def _glob_cookies(pattern: str) -> list[Path]:
    seen: set[Path] = set()
    for d in _dirs():
        try:
            seen |= set(d.glob(pattern))
        except (PermissionError, OSError):
            continue
    return sorted(seen)


def is_logged_in(plat: str) -> bool:
    """只看文件存在与体积，不发网络请求。

    真伪由 SAU 的 `check` 子命令判定；面板上标「已登录」只代表 cookie 落盘了，
    这一点在 UI 上要写清楚，避免用户以为一定有效。
    """
    best = 0
    for d in _dirs():
        p = d / f"{plat}_{ACCOUNT}.json"
        try:
            if p.exists():
                best = max(best, p.stat().st_size)
        except (PermissionError, OSError):
            continue
    return best >= MIN_COOKIE_BYTES


def cookie_info(plat: str) -> dict:
    for d in _dirs():
        p = d / f"{plat}_{ACCOUNT}.json"
        try:
            if not p.exists():
                continue
            st = p.stat()
        except (PermissionError, OSError):
            continue
        return {
            "exists": True,
            "bytes": st.st_size,
            "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
            "path": str(p),
        }
    return {"exists": False}


# ── 登录流程 ───────────────────────────────────────────────────────────────

def _qr_snapshot(plat: str) -> set[Path]:
    # 文件名不统一：kuaishou_.._ks_login_qrcode_*、hupu_.._qq_qrcode.png
    pats = [f"{plat}*qrcode*.png", f"{plat}*qr*.png"]
    found: set[Path] = set()
    # QR_EXPORT 优先：~ 卷，launchd 可自由读取
    for pat in pats:
        try:
            found |= set(QR_EXPORT.glob(pat))
        except (PermissionError, OSError):
            pass
    for pat in pats:
        found |= set(_glob_cookies(pat))
    return found


def _qr_from_log(plat: str) -> list[Path]:
    """从 SAU 日志里抠出二维码的绝对路径。

    为什么不能靠 glob：launchd 进程 listdir(~/Desktop/...) 会 PermissionError，
    而二维码就落在 Desktop 下的 cookies 目录里。但对**已知绝对路径**做 stat/copy
    是允许的。SAU 自己会把路径打进日志（「🖼️ 二维码已保存到: ...」），
    日志在 ~ 卷下可读，于是「日志给路径 + 按路径取文件」绕过了目录枚举限制。
    """
    log = SCRATCH / f"dock_{plat}.log"
    out: list[Path] = []
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, PermissionError, OSError):
        return out
    for m in re.finditer(r"(/[^\s]+?\.png)", text):
        p = Path(m.group(1))
        if p.suffix == ".png" and plat in p.name:
            out.append(p)
    return out


def _latest_qr(plat: str, since: float) -> Path | None:
    """取 start_ts 之后生成的最新二维码文件。

    两条来源互补：
      1. 日志里的绝对路径 —— launchd 下唯一可靠的来源（绕过 listdir 限制）；
      2. 镜像/仓库目录 glob —— 交互式终端下更快，也覆盖日志还没刷盘的窗口。
    不能用「差分集合」判断新二维码：SAU 登录成功或超时后会**删掉**二维码 png，
    差分法会在文件被删的那个瞬间漏掉它。改成按 mtime 判定就不受删除影响。
    """
    candidates: set[Path] = _qr_snapshot(plat)
    best: Path | None = None
    best_m = 0.0
    for f in candidates:
        try:
            m = f.stat().st_mtime
        except (FileNotFoundError, PermissionError, OSError):
            continue
        if m >= since - 2 and m > best_m:   # 容 2 秒时钟抖动
            best, best_m = f, m
    return best


def _sync_mirror() -> None:
    """把 cookies 从 Desktop 镜像到 ~ 卷。

    launchd 进程不能 listdir(~/Desktop)，只能对已知路径 stat。这里的已知路径
    来自平台注册表（平台名 + 固定文件命名），逐个 stat → 有变化就 copy。
    """
    COOKIES_MIRROR.mkdir(parents=True, exist_ok=True)
    try:
        plats = [p["platform"] for p in platform_registry.build()["platforms"]]
    except Exception:
        plats = list(_status.keys())
    for plat in plats:
        src = COOKIES / f"{plat}_{ACCOUNT}.json"
        try:
            if not src.exists():
                continue
            st = src.stat()
        except (PermissionError, OSError):
            continue
        dst = COOKIES_MIRROR / src.name
        try:
            if not dst.exists() or dst.stat().st_mtime < st.st_mtime:
                shutil.copy2(src, dst)
        except (PermissionError, OSError):
            continue


def _dbg(msg: str) -> None:
    """登录坞自诊断日志。

    线程里的异常默认只到 stderr，launchd 下极易丢失；UI 会永远停在
    「正在打开浏览器…」而完全无线索。所以关键节点一律落文件。
    """
    try:
        p = Path(os.path.expanduser("~/CreatorStudio/logs/logindock-debug.log"))
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _watch(plat: str, before: set[Path], proc: subprocess.Popen) -> None:
    """后台盯二维码与 cookie，直到成功/失败/超时。

    整个函数包在 try 里：线程里抛异常默认只打到 stderr 且进程照常活着，
    结果是 UI 永远停在「正在打开浏览器…」——这是上一版最难查的故障。
    """
    try:
        _watch_inner(plat, before, proc)
    except Exception as exc:  # noqa: BLE001
        _dbg(f"{plat} watch 异常 {type(exc).__name__}: {exc}")
        _set(plat, "fail", f"监控线程异常：{type(exc).__name__}: {exc}"[:160])
        with _lock:
            _running.pop(plat, None)


def _watch_inner(plat: str, before: set[Path], proc: subprocess.Popen) -> None:
    start_ts = _started_at.get(plat, time.time())
    _dbg(f"{plat} watch start ts={start_ts:.0f} proc={proc.pid}")
    deadline = time.time() + 300
    copied: Path | None = None
    while time.time() < deadline:
        cp = cookie_path(plat)
        size = 0
        try:
            size = cp.stat().st_size if cp.exists() else 0
        except (PermissionError, OSError):
            size = 0
        if size >= MIN_COOKIE_BYTES:
            _sync_mirror()
            _dbg(f"{plat} cookie 落盘 {size}B → ok")
            _set(plat, "ok", f"已登录（{size}B）")
            with _lock:
                _running.pop(plat, None)
            return

        latest = _latest_qr(plat, start_ts)
        if latest and latest != copied:
            dst = OUT / f"{plat}.png"
            try:
                shutil.copy2(latest, dst)
                copied = latest
                _dbg(f"{plat} 二维码已复制 {latest.name} → {dst}")
                _set(plat, "qr", "扫码中")
            except (FileNotFoundError, OSError) as exc:
                _dbg(f"{plat} 复制二维码失败 {exc}")

        if proc.poll() is not None:
            # 进程退出：若此时 cookie 已落盘则算成功（cookie 是本次或之前写的都认，
            # 因为 SAU 只在登录成功后才落盘；之前登录过的平台不会被这条误判）
            size2 = 0
            try:
                size2 = cp.stat().st_size if cp.exists() else 0
            except (PermissionError, OSError):
                size2 = 0
            if size2 >= MIN_COOKIE_BYTES:
                _sync_mirror()
                _set(plat, "ok", f"已登录（{size2}B）")
            else:
                _set(plat, "fail", "登录未完成（二维码超时或页面加载失败），可重试")
            with _lock:
                _running.pop(plat, None)
            return
        time.sleep(0.8)

    _set(plat, "timeout", "二维码已过期，请重新点击")
    with _lock:
        _running.pop(plat, None)


def _set(plat: str, status: str, msg: str) -> None:
    with _lock:
        _status[plat] = {"status": status, "msg": msg, "at": time.time()}


def start_login(plat: str) -> dict:
    reg = platform_registry.build()
    entry = next((p for p in reg["platforms"] if p["platform"] == plat), None)
    if not entry:
        return {"ok": False, "msg": f"未知平台 {plat}"}

    mode = entry["loginMode"]
    if mode == "cookie":
        return {
            "ok": False,
            "msg": "该平台无法扫码登录，只能导入 cookie 文件到 "
                   f"{COOKIES}/{plat}_{ACCOUNT}.json",
        }
    if mode == "qr_terminal":
        return {
            "ok": False,
            "msg": "该平台必须在本地终端运行：cd "
                   f"{BASE} && ./.venv/bin/python sau_cli.py {plat} login --account {ACCOUNT}",
        }

    with _lock:
        already = _running.get(plat)
        if already and already.poll() is None:
            return {"ok": True, "msg": "已在等待扫码"}
        others = [(p, pr) for p, pr in _running.items() if pr.poll() is None]
        if others:
            return {
                "ok": False,
                "msg": f"请先完成 {others[0][0]} 的登录（一次一个平台，避免浏览器抢占）",
            }

    before = _qr_snapshot(plat)
    argv = [str(PY), "sau_cli.py", plat, "login", "--account", ACCOUNT]
    if entry.get("acceptsHeaded"):
        argv.append("--headed")

    OUT.mkdir(parents=True, exist_ok=True)
    log = SCRATCH / f"dock_{plat}.log"
    fh = open(log, "w")
    # SAU 保存二维码时会额外写一份到 QR_EXPORT（~ 卷）。登录坞是 launchd 进程，
    # 读不到 Desktop 里文件的内容（TCC），只能读 ~ 卷 —— 这是必需的通道。
    env = dict(os.environ)
    env["SAU_QR_EXPORT_DIR"] = str(QR_EXPORT)
    try:
        proc = subprocess.Popen(argv, cwd=str(BASE), stdout=fh,
                                stderr=subprocess.STDOUT, start_new_session=True,
                                env=env)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "msg": f"启动失败：{exc}"}

    with _lock:
        _running[plat] = proc
        _started_at[plat] = time.time()
    _set(plat, "starting", "正在打开浏览器…")
    threading.Thread(target=_watch, args=(plat, before, proc), daemon=True).start()
    return {"ok": True, "msg": "已启动，3–20 秒后二维码会出现"}


# ── HTTP ───────────────────────────────────────────────────────────────────

PAGE = r"""<!doctype html><html lang="zh"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>扫码登录 · 宣发工作台</title>
<style>
:root{color-scheme:light dark;--bg:#f6f7f9;--fg:#1a1a1a;--card:#fff;
 --bd:#e3e6ea;--mut:#666;--pri:#2563eb;--ok:#15803d;--okbg:#dcfce7;
 --qrbg:#dbeafe;--qrfg:#1d4ed8;--err:#b91c1c;--errbg:#fee2e2}
@media(prefers-color-scheme:dark){:root{--bg:#16181d;--fg:#e6e8eb;--card:#1e2127;
 --bd:#2c3038;--mut:#9aa3ad;--okbg:#14361f;--ok:#6ee7a0;--qrbg:#16304d;
 --qrfg:#8ab4f8;--errbg:#3a1a1a;--err:#f88}}
*{box-sizing:border-box}
body{font:14px/1.6 -apple-system,"PingFang SC",sans-serif;margin:0;padding:24px;
 background:var(--bg);color:var(--fg)}
h1{font-size:19px;margin:0 0 6px}
.sub{color:var(--mut);font-size:12.5px;margin-bottom:18px;line-height:1.8}
.stats{display:flex;gap:8px;margin-bottom:18px;flex-wrap:wrap}
.pill{font-size:12px;padding:5px 12px;border-radius:99px;background:var(--card);
 border:1px solid var(--bd)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(238px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;
 padding:14px;display:flex;flex-direction:column;gap:10px}
.card.done{border-color:var(--ok)}
.name{font-weight:600;display:flex;justify-content:space-between;align-items:center;gap:8px}
.tag{font-size:11px;padding:2px 9px;border-radius:99px;background:var(--bd);
 color:var(--mut);white-space:nowrap}
.tag.ok{background:var(--okbg);color:var(--ok)}
.tag.qr{background:var(--qrbg);color:var(--qrfg)}
.tag.err{background:var(--errbg);color:var(--err)}
.qrbox{min-height:168px;display:flex;align-items:center;justify-content:center;
 background:var(--bg);border-radius:10px;border:1px dashed var(--bd);padding:8px}
.qrbox img{width:164px;height:164px;image-rendering:pixelated;border-radius:6px}
.ph{color:var(--mut);font-size:12px;text-align:center;padding:0 10px}
.meta{font-size:11.5px;color:var(--mut);min-height:32px;line-height:1.5}
button{font:inherit;padding:8px 12px;border-radius:8px;border:1px solid var(--bd);
 background:var(--card);color:var(--fg);cursor:pointer;width:100%}
button:hover:not(:disabled){filter:brightness(.97)}
button.primary{background:var(--pri);color:#fff;border-color:var(--pri)}
button:disabled{opacity:.5;cursor:not-allowed}
.cmd{font-family:ui-monospace,Menlo,monospace;font-size:10.5px;background:var(--bg);
 padding:7px 9px;border-radius:7px;word-break:break-all;color:var(--mut)}
</style>
<h1>扫码登录</h1>
<div class="sub">
  点「生成二维码」→ 用对应 App 扫 → cookie 自动落盘。<br>
  只做登录，<b>不发布任何内容</b>。账号别名 <b>__ACCOUNT__</b>
</div>
<div class="stats" id="stats"></div>
<div class="grid" id="g"></div>
<script>
const ACCOUNT = "__ACCOUNT__";
let REG = [];
async function api(p, opt){ const r = await fetch(p, opt); return r.json(); }
function cls(st){
  if(st==='ok') return 'ok';
  if(st==='qr'||st==='starting') return 'qr';
  if(st==='fail'||st==='timeout') return 'err';
  return '';
}
function tagOf(p, st){
  if(st && st.status) return [st.status, cls(st.status)];
  if(p.loggedIn) return ['已登录','ok'];
  if(p.loginMode==='cookie') return ['需cookie',''];
  return ['未登录',''];
}
async function render(){
  const s = await api('/api/state');
  REG = s.platforms;
  const done = REG.filter(p=>p.loggedIn).length;
  document.getElementById('stats').innerHTML =
    `<span class="pill">已接入 <b>${done}</b> / ${REG.length}</span>`
    + `<span class="pill">扫码可接入 ${REG.filter(p=>p.loginMode==='qr').length}</span>`
    + `<span class="pill">需终端 ${REG.filter(p=>p.loginMode==='qr_terminal').length}</span>`
    + `<span class="pill">需 cookie ${REG.filter(p=>p.loginMode==='cookie').length}</span>`;

  document.getElementById('g').innerHTML = REG.map(p=>{
    const st = s.status[p.platform] || {};
    const [txt, c] = tagOf(p, st);
    const hasQR = s.qr && s.qr[p.platform];
    const canQR = (p.loginMode==='qr'||p.loginMode==='browser');
    let body='';
    if(hasQR && (st.status==='qr'||st.status==='starting'||p.loggedIn)){
      body = `<img src="/qr/${p.platform}.png?t=${Date.now()}">`;
    } else if(p.loginMode==='cookie'){
      body = `<div class="ph">需导入 cookie 文件<br>${p.platform}_${ACCOUNT}.json</div>`;
    } else if(p.loggedIn){
      body = `<div class="ph">已登录<br>${p.cookieBytes||''}B</div>`;
    } else {
      body = `<div class="ph">点下方按钮生成二维码</div>`;
    }
    const scan = p.scanApp ? `用「${p.scanApp}」扫` : '';
    let btn = '';
    if(p.loggedIn){
      btn = `<button onclick="act('${p.platform}')">重新登录</button>`;
    } else if(canQR){
      btn = `<button class="primary" onclick="act('${p.platform}')">生成二维码</button>`;
    } else if(p.loginMode==='qr_terminal'){
      btn = `<div class="cmd">./.venv/bin/python sau_cli.py ${p.platform} login --account ${ACCOUNT}</div>`;
    } else {
      btn = `<button disabled>需手动导入</button>`;
    }
    return `<div class="card ${p.loggedIn?'done':''}" id="${p.platform}">
      <div class="name">${p.label}<span class="tag ${c}">${txt}</span></div>
      <div class="qrbox">${body}</div>
      <div class="meta">${st.msg||p.note||scan||''}</div>
      ${btn}
    </div>`;
  }).join('');
  // 从主面板带 #platform 跳过来时，滚到对应卡片并高亮
  if(location.hash){
    const el = document.getElementById(location.hash.slice(1));
    if(el){ el.scrollIntoView({block:'center'}); el.style.outline='2px solid var(--pri)'; }
  }
}
async function act(plat){
  const j = await api('/api/start/'+plat, {method:'POST'});
  if(!j.ok){ alert(j.msg); }
  render();
}
render();
setInterval(render, 2500);
</script></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text: str) -> None:
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]

        if path == "/":
            self._html(PAGE.replace("__ACCOUNT__", ACCOUNT))
            return

        if path == "/api/platforms":
            self._json(platform_registry.build())
            return

        if path == "/api/state":
            reg = platform_registry.build()
            with _lock:
                status = {k: v for k, v in _status.items() if not k.startswith("_")}
            plats = []
            for p in reg["platforms"]:
                info = cookie_info(p["platform"])
                plats.append({
                    **p,
                    "loggedIn": info.get("exists", False),
                    "cookieBytes": info.get("bytes"),
                    "cookieTime": info.get("mtime"),
                })
            qr = {p["platform"]: (OUT / f"{p['platform']}.png").exists()
                  for p in reg["platforms"]}
            self._json({"platforms": plats, "status": status, "qr": qr})
            return

        if path.startswith("/qr/"):
            f = OUT / path[4:]
            if f.exists() and f.suffix == ".png":
                data = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json({"error": "not found"}, 404)
            return

        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path.startswith("/api/start/"):
            self._json(start_login(path[len("/api/start/"):]))
            return
        self._json({"error": "not found"}, 404)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8898)
    ap.add_argument("--account", default=None,
                    help=f"账号别名（默认 {ACCOUNT}）")
    args = ap.parse_args()

    # 账号别名供各处理器读取模块全局；不能在这里写 global ACCOUNT，
    # 因为 main() 上方的参数默认值已经引用了 ACCOUNT。
    if args.account:
        globals()["ACCOUNT"] = args.account

    OUT.mkdir(parents=True, exist_ok=True)
    # 启动时预热注册表，避免首屏空白
    platform_registry.build()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"扫码登录坞: http://127.0.0.1:{args.port}/  (account={globals()['ACCOUNT']})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
