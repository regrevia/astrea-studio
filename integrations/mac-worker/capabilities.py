"""平台账号与配音能力清单。

统一面板要回答两个问题：
1. 哪些平台现在能发（cookie 有效）、哪些要重新登录；
2. 现在能用哪些配音音色。

SAU 的 cookie 落盘规则是 ``cookies/{platform}_{account}.json``，本模块只读
文件、不启动浏览器、不触碰任何发布动作 —— 纯本地探测，零网络、零 token。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

STUDIO_HOME = Path.home() / "CreatorStudio"

# launchd 进程读不到 ~/Desktop（TCC 保护），所以仓库那份 cookie 目录它看不见。
# worker 侧每次同步时会把 cookies 镜像到 ~/CreatorStudio/state/，两处都探测。
_REPO = Path.home() / "Desktop/work/Studio/creator-studio/integrations/vendor/social-auto-upload"
_RUNTIME = Path(__file__).resolve().parent.parent / "vendor" / "social-auto-upload"
_MIRROR = STUDIO_HOME / "state" / "sau"

_CANDIDATES = [_MIRROR, _RUNTIME, _REPO]
SAU_DIR = next((p for p in _CANDIDATES if (p / "cookies").is_dir() and any((p / "cookies").glob("*.json"))),
               next((p for p in _CANDIDATES if (p / "cookies").is_dir()), _RUNTIME))
COOKIE_DIR = SAU_DIR / "cookies"

# MPT 同理，多处探测。注意顺序：镜像优先 —— launchd 读不到 ~/Desktop，
# 若把 Desktop 放前面，探测会命中「文件存在判定失败」而退到不存在的路径。
_MPT_CANDIDATES = [
    STUDIO_HOME / "state" / "MoneyPrinterTurbo",
    _REPO.parent / "MoneyPrinterTurbo",
    _RUNTIME.parent / "MoneyPrinterTurbo",
]
MPT_DIR = next((p for p in _MPT_CANDIDATES if (p / "app/services/sami_tts.py").exists()),
               _MPT_CANDIDATES[0])

# SAU 支持的平台 → 面板显示名。
# 这张表只作为「注册表探测失败时的兜底」——平台清单的唯一真相源是
# platform_registry.py（它从 sau_cli.py 自动发现，上游加平台会自动跟上）。
FALLBACK_LABELS = {
    "douyin": "抖音",
    "xiaohongshu": "小红书",
    "bilibili": "B站",
    "kuaishou": "快手",
    "tencent": "视频号",
    "weibo": "微博",
    "zhihu": "知乎",
    "baijiahao": "百家号",
    "hupu": "虎扑",
    "xueqiu": "雪球",
    "alipay": "支付宝",
    "dayu": "大鱼号",
    "youtube": "YouTube",
    "instagram": "Instagram",
    "facebook": "Facebook",
}

# 支持图文（upload-note）的平台子集（注册表缺失时兜底）
NOTE_PLATFORMS = {"douyin", "xiaohongshu", "bilibili", "kuaishou", "tencent", "weibo", "baijiahao"}


def _registry() -> dict | None:
    """读平台注册表。失败返回 None，调用方退回兜底表。"""
    try:
        import platform_registry
        return platform_registry.build()
    except Exception:
        return None


@dataclass
class PlatformAccount:
    platform: str
    label: str
    account: str
    cookieFile: str
    cookieBytes: int
    hasNote: bool
    mtime: str


def _labels() -> dict[str, str]:
    """平台 → 中文名。优先注册表，缺失则兜底。"""
    reg = _registry()
    if reg and reg.get("platforms"):
        return {p["platform"]: p["label"] for p in reg["platforms"]}
    return dict(FALLBACK_LABELS)


def _cookie_accounts() -> list[PlatformAccount]:
    """扫描 cookies 目录，解析出「哪个平台、哪个账号、什么时候登录的」。"""
    out: list[PlatformAccount] = []
    if not COOKIE_DIR.is_dir():
        return out
    labels = _labels()
    for f in sorted(COOKIE_DIR.glob("*.json")):
        stem = f.stem                      # e.g. douyin_yefen
        if "_" not in stem:
            continue
        platform, account = stem.split("_", 1)
        # 只认注册表里存在的平台；但账号名可能含下划线，所以要能容忍
        if platform not in labels and platform.replace("-", "") not in {
                k.replace("-", "") for k in labels}:
            continue
        try:
            from datetime import datetime
            mtime = datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")
        except Exception:
            mtime = ""
        out.append(PlatformAccount(
            platform=platform,
            label=labels.get(platform, platform),
            account=account,
            cookieFile=str(f),
            cookieBytes=f.stat().st_size,
            hasNote=platform in NOTE_PLATFORMS,
            mtime=mtime,
        ))
    return out


def check_platform_cookie(platform: str, account: str, timeout: int = 120) -> dict:
    """调 SAU 的 check 子命令验证 cookie 是否仍然有效。

    注意：``check`` 只读 cookie 并访问平台首页，不上传任何内容。
    """
    venv_py = SAU_DIR / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.exists() else "python3"
    cmd = [py, "sau_cli.py", platform, "check", "--account", account]
    try:
        res = subprocess.run(
            cmd, cwd=str(SAU_DIR), capture_output=True, text=True, timeout=timeout,
        )
        blob = (res.stdout or "") + (res.stderr or "")
        valid = "valid" in blob.lower()
        return {
            "platform": platform,
            "account": account,
            "valid": valid,
            "exitCode": res.returncode,
            "message": blob.strip().splitlines()[-1][:200] if blob.strip() else "",
        }
    except subprocess.TimeoutExpired:
        return {"platform": platform, "account": account, "valid": False,
                "exitCode": -1, "message": "check timeout"}
    except Exception as exc:
        return {"platform": platform, "account": account, "valid": False,
                "exitCode": -1, "message": f"{type(exc).__name__}: {exc}"[:200]}


# ---------------------------------------------------------------- 配音音色

def jianying_voices() -> list[dict]:
    """剪映原生音色（SAMI）清单。

    不从模块导入（那需要 loguru/torch 等 MPT 依赖），而是直接解析
    ``sami_tts.py`` 里的 SPEAKER_ALIASES 常量 —— 零依赖、零副作用。
    """
    import re
    sa = MPT_DIR / "app" / "services" / "sami_tts.py"
    if not sa.exists():
        return []
    try:
        blob = sa.read_text(encoding="utf-8")
    except OSError:
        return []
    m = re.search(r"SPEAKER_ALIASES\s*=\s*\{(.*?)\n\}", blob, re.S)
    if not m:
        return []
    pairs = re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', m.group(1))
    return [{"id": sid, "name": name, "voice": f"sami:{sid}"} for name, sid in pairs]


def edge_voices() -> list[dict]:
    """Edge TTS 的 zh-CN 音色。"""
    return [
        {"id": "zh-CN-YunxiNeural", "name": "云希（磁性男声）"},
        {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓（通用女声）"},
        {"id": "zh-CN-YunjianNeural", "name": "云健（激情男声）"},
        {"id": "zh-CN-YunyangNeural", "name": "云扬（专业男声）"},
        {"id": "zh-CN-YunxiaNeural", "name": "云夏（少年）"},
        {"id": "zh-CN-XiaoyiNeural", "name": "晓伊（活泼女声）"},
        {"id": "zh-CN-YunfengNeural", "name": "云枫"},
        {"id": "zh-CN-liaoning-XiaobeiNeural", "name": "晓北（东北话）"},
        {"id": "zh-CN-shaanxi-XiaoniNeural", "name": "晓妮（陕西话）"},
    ]


def capabilities() -> dict:
    """一次性汇总面板需要的全部能力状态（纯本地读取）。"""
    accounts = _cookie_accounts()
    by_platform: dict[str, list[dict]] = {}
    for a in accounts:
        by_platform.setdefault(a.platform, []).append({
            "account": a.account, "cookieBytes": a.cookieBytes,
            "hasNote": a.hasNote, "mtime": a.mtime, "cookieFile": a.cookieFile,
        })

    platforms = [
        {
            "platform": p,
            "label": label,
            "ready": bool(by_platform.get(p)),
            "accounts": by_platform.get(p, []),
            "supportsNote": p in NOTE_PLATFORMS,
        }
        for p, label in _labels().items()
    ]

    return {
        "platforms": platforms,
        "summary": {
            "total": len(platforms),
            "ready": sum(1 for p in platforms if p["ready"]),
            "accounts": len(accounts),
        },
        "voices": {
            "jianying": jianying_voices(),
            "edge": edge_voices(),
        },
        "tools": {
            "ffmpeg": _which_version("ffmpeg", ["-version"]),
            "vhs": _which_version("vhs", ["--version"]),
            "shot-scraper": _which_version("uvx", ["shot-scraper", "--version"]),
        },
    }


def _which_version(cmd: str, args: list[str]) -> dict:
    import shutil
    path = shutil.which(cmd)
    if not path:
        return {"available": False, "version": ""}
    try:
        res = subprocess.run([path, *args], capture_output=True, text=True, timeout=25)
        first = (res.stdout or res.stderr or "").strip().splitlines()
        return {"available": True, "version": first[0][:80] if first else ""}
    except Exception:
        return {"available": True, "version": ""}


if __name__ == "__main__":
    print(json.dumps(capabilities(), ensure_ascii=False, indent=2))
