#!/usr/bin/env python3
"""平台注册表：从 SAU 自动发现平台，不硬编码任何平台清单。

为什么必须自动发现：SAU 的 `sau_cli.py` 自己维护平台清单（当前 17 个），
上游加一个平台我们的面板就该自动多一个卡片。硬编码清单的结果是
「面板说 15 个平台，实际 SAU 有 17 个」——用户看到的能力永远落后于真实能力。

做法：跑 `sau_cli.py --help` 拿平台列表，再逐个 `sau_cli.py <plat> --help`
拿子命令（login/check/upload-video/upload-note/verify/delete），
最后按源码特征判定登录方式。结果缓存到 JSON，避免每次启动都探测。

登录方式判定（决定 UI 给什么按钮）：
  qr            扫码，可从面板直接拉起
  qr_terminal   扫码但 SAU 强制要求交互终端（bilibili），只能终端跑
  browser       要可见浏览器里交互登录，面板可拉起但需人操作
  cookie        只能导入 cookie 文件，面板不提供按钮
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

BASE = Path(os.path.expanduser(
    "~/Desktop/work/Studio/creator-studio/integrations/vendor/social-auto-upload"))
PY = BASE / ".venv/bin/python"
CACHE = Path(os.path.expanduser("~/CreatorStudio/config/platform-registry.json"))
CACHE_TTL = 3600  # 一小时

# 中文显示名
LABELS = {
    "douyin": "抖音", "xiaohongshu": "小红书", "bilibili": "B站",
    "kuaishou": "快手", "tencent": "视频号", "weibo": "微博",
    "zhihu": "知乎", "baijiahao": "百家号", "hupu": "虎扑",
    "xueqiu": "雪球", "alipay": "支付宝", "dayu": "大鱼号",
    "youtube": "YouTube", "instagram": "Instagram", "facebook": "Facebook",
    "facebook-page": "Facebook 主页", "article": "通用文章",
}

# 扫码用的 App（提示用户用哪个 App 扫）
SCAN_APP = {
    "douyin": "抖音", "xiaohongshu": "小红书", "bilibili": "哔哩哔哩",
    "kuaishou": "快手", "tencent": "微信", "weibo": "微博",
    "zhihu": "知乎", "baijiahao": "百度", "hupu": "QQ",
    "xueqiu": "雪球", "alipay": "支付宝",
}


def _run(args: list[str], timeout: int = 60) -> str:
    try:
        r = subprocess.run([str(PY), "sau_cli.py", *args], cwd=str(BASE),
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout + r.stderr
    except Exception:
        return ""


def _discover_platforms() -> list[str]:
    """从根 help 里解析平台名。"""
    out = _run(["--help"])
    # usage 行里有 {a,b,c}
    m = re.search(r"\{([a-z0-9\-,]+)\}", out)
    if not m:
        return []
    return [p for p in m.group(1).split(",") if p and p != "article"]


def _subcommands(plat: str) -> list[str]:
    out = _run([plat, "--help"])
    m = re.search(r"\{([a-z0-9\-,]+)\}", out)
    if not m:
        return []
    return [s for s in m.group(1).split(",") if s]


def _source_of(plat: str) -> str:
    """把该平台相关的源码拼起来，用于判定登录方式。

    uploader 目录名和平台 id 常常对不上（kuaishou → ks_uploader、
    xiaohongshu → xiaohongshu_uploader + xhs_uploader），所以不能只按平台名
    找目录。策略是：先看 sau_commands/<plat>.py 里 import 了哪些 uploader 模块，
    顺着 import 去读真实实现；读不到再退回按目录名模糊匹配。
    """
    candidates: list[Path] = []
    cmd_file = BASE / "sau_commands" / f"{plat}.py"
    if cmd_file.exists():
        candidates.append(cmd_file)
        src = cmd_file.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"from\s+uploader\.([a-z0-9_]+)", src):
            pkg = m.group(1)
            d = BASE / "uploader" / pkg
            if d.is_dir():
                candidates.extend(d.glob("*.py"))

    # 兜底：目录名模糊匹配（含平台名前 4 位或已知别名）
    ALIAS = {"kuaishou": "ks", "xiaohongshu": "xhs", "tencent": "tencent",
             "baijiahao": "baijiahao", "youtube": "youtube", "dayu": "dayu"}
    keys = {plat, plat.split("-")[0]}
    if plat in ALIAS:
        keys.add(ALIAS[plat])
    for key in keys:
        for d in (BASE / "uploader").glob(f"*{key}*"):
            if d.is_dir():
                candidates.extend(d.glob("*.py"))

    seen: set[Path] = set()
    blob = ""
    for c in candidates:
        if c in seen or not c.exists():
            continue
        seen.add(c)
        blob += c.read_text(encoding="utf-8", errors="replace")
    return blob


def _classify(plat: str, subs: list[str], blob: str) -> dict:
    """判定登录方式与 UI 能力。"""
    low = blob.lower()
    has_login = "login" in subs

    mode = "cookie"
    note = ""
    if not has_login:
        mode = "cookie"
        note = "该平台无 login 子命令，只能导入 cookie"
    elif "requires a local interactive terminal" in low:
        # bilibili：SAU 明确拒绝非交互环境
        mode = "qr_terminal"
        note = "需在本地终端运行（SAU 限制）"
    elif re.search(r"qrcode|二维码|qr_code|qq_qrcode", low):
        mode = "qr"
        note = ""
    elif re.search(r"可见\s*chrome|visible.*chrome", low):
        mode = "browser"
        note = "需在可见浏览器里手动完成登录"

    return {
        "platform": plat,
        "label": LABELS.get(plat, plat),
        "scanApp": SCAN_APP.get(plat, ""),
        "loginMode": mode,
        "note": note,
        # 有些平台的 login 不接受 --headed
        "acceptsHeaded": "--headed" in blob and plat not in ("bilibili", "zhihu", "xueqiu"),
        "subcommands": subs,
        "canVideo": "upload-video" in subs,
        "canNote": "upload-note" in subs,
        "canArticle": "upload-article" in subs,
        "canVerify": "verify" in subs,
        "canDelete": "delete" in subs,
    }


def build(force: bool = False) -> dict:
    if not force and CACHE.exists():
        try:
            d = json.loads(CACHE.read_text(encoding="utf-8"))
            if time.time() - d.get("generatedAt", 0) < CACHE_TTL:
                return d
        except Exception:
            pass

    plats = _discover_platforms()
    items = []
    for p in plats:
        subs = _subcommands(p)
        blob = _source_of(p)
        items.append(_classify(p, subs, blob))

    data = {
        "generatedAt": time.time(),
        "sauRoot": str(BASE),
        "total": len(items),
        "platforms": items,
    }
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


if __name__ == "__main__":
    import sys
    d = build(force="--force" in sys.argv)
    print(f"发现 {d['total']} 个平台（缓存 {CACHE}）")
    for p in d["platforms"]:
        types = "/".join([t for t, k in
                          [("视频", "canVideo"), ("图文", "canNote"),
                           ("文章", "canArticle")] if p[k]] or ["只读"])
        print(f"  {p['label']:12s} {p['loginMode']:12s} {types:10s} {p['note']}")
