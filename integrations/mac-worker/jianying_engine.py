#!/usr/bin/env python3
"""jianying_engine.py — 剪映草稿引擎（VectCutAPI 驱动）

与其它引擎的分工：
  slides  = 静态图文定格（快，PPT 感）
  motion  = GSAP 时间轴 + 镜头硬切（慢，有动效语言）
  chalk   = 黑板粉笔风格（手绘质感）
  jianying= 只生成剪映草稿，把转场/特效/字体/文字动画交给剪映原生资源

为什么需要这一条腿：剪映原生的 453 种转场、1097 种画面特效、1052 种滤镜、
145 种文字入场动画是自研渲染器永远追不上的。代价是 macOS 上导出必须人工点。

协议（spec["native"]）：
  scenes[].native = {
    "transition_in":  "黑板擦除",      # 进场转场名（中文，走 asset_search 解析）
    "text_anim_in":   "描边粉笔",      # 文字入场动画
    "filter":         "黑白",          # 滤镜
    "effect":         "复古",          # 画面特效
    "font":           "字魂镇魂手书",   # 字体
  }
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

class JianyingError(RuntimeError):
    pass


HOME = Path.home()
VCUT_DIR = HOME / "CreatorStudio" / "jianying" / "VectCutAPI"
VCUT_PY = VCUT_DIR / ".venv" / "bin" / "python"
VCUT_PORT = int(os.getenv("VCUT_PORT", "9100"))
VCUT_BASE = f"http://127.0.0.1:{VCUT_PORT}"

DRAFT_ROOT = HOME / "Movies" / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"


# ---------------------------------------------------------------- HTTP 层

def _post(path: str, payload: dict[str, Any], timeout: int = 600) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{VCUT_BASE}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise JianyingError(f"VectCutAPI unreachable at {VCUT_BASE}: {exc}") from exc
    if not body.get("success"):
        raise JianyingError(f"{path} failed: {body.get('error') or body}")
    return body.get("output") or {}


def _get(path: str, timeout: int = 60) -> dict[str, Any]:
    with urllib.request.urlopen(f"{VCUT_BASE}{path}", timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("output") or {}


def ensure_service() -> None:
    """VectCutAPI 没起就拉起来（幂等）。"""
    try:
        _get("/list_projects", timeout=5)
        return
    except Exception:
        pass
    if not VCUT_PY.exists():
        raise JianyingError(
            f"VectCutAPI venv missing: {VCUT_PY}\n"
            "首次使用请运行: bash ~/CreatorStudio/jianying/install_vcut.sh")
    subprocess.Popen(
        [str(VCUT_PY), "capcut_server.py"], cwd=str(VCUT_DIR),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    import time
    for _ in range(40):
        time.sleep(0.5)
        try:
            _get("/list_projects", timeout=3)
            return
        except Exception:
            continue
    raise JianyingError("VectCutAPI failed to start within 20s")


# ---------------------------------------------------------------- 草稿部署

def _deploy(draft_dir: Path, project_name: str) -> Path:
    """把 VectCutAPI 产出的草稿拷进剪映草稿根目录（macOS 无 auto_deploy）。"""
    DRAFT_ROOT.mkdir(parents=True, exist_ok=True)
    dest = DRAFT_ROOT / project_name
    if dest.exists():
        backup = dest.with_suffix(dest.suffix + ".bak")
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        dest.rename(backup)
    shutil.copytree(draft_dir, dest)

    meta_path = dest / "draft_meta_info.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["draft_name"] = project_name
        meta["draft_fold_path"] = str(dest)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.utime(dest, None)
    return dest


# ---------------------------------------------------------------- spec 适配

def _asset_lookup(name: str, category: str) -> str | None:
    """中文名 → 剪映资源标识。三级回退，任一命中即可。

    1) isYangs 的 asset_search（CSV 库 + 同义词扩展）：覆盖最广，
       输出结构 {"ok":true,"data":{"results":[{"identifier":"..."}]}}，
       且 stdout 前缀 "[INFO] Searching ..." 必须跳过。
    2) pyJianYingDraft 枚举的精确成员名（如 Transition_type.画笔擦除）。
    3) 原始名直接透传（调用方拿不到就退回原值）。

    实测差异：isYangs CSV 里有 `黑板擦除`，但 pyJianYingDraft 0.3.1 的
    Transition_type 只有 `画笔擦除`／`渐变擦除`／`水墨`——两者版本不同步，
    所以必须双查，不能只信一个来源。
    """
    if not name:
        return None

    # --- 1) CSV 库
    searcher = HOME / "CreatorStudio" / "jianying" / "skill" / "scripts" / "asset_search.py"
    if searcher.exists():
        try:
            out = subprocess.run(
                [sys.executable, str(searcher), name, "-c", category, "-l", "1", "--json"],
                capture_output=True, text=True, timeout=60)
            payload = None
            for line in (out.stdout or "").splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
            results = (payload or {}).get("data", {}).get("results") or []
            if results:
                row = results[0]
                got = row.get("identifier") or row.get("api_key") or row.get("name")
                if got:
                    return got
        except Exception:
            pass

    # --- 2) pyJianYingDraft 枚举
    enum_map = {
        "transitions": "Transition_type",
        "text_animations": "Text_intro",
        "text_intro_animations": "Text_intro",
        "text_outro_animations": "Text_outro",
        "filters": "Filter",
        "video_scene_effects": "Video_scene_effect_type",
    }
    enum_name = enum_map.get(category)
    if enum_name:
        try:
            import pyJianYingDraft as draft
            enum_obj = getattr(draft, enum_name, None)
            if enum_obj is not None and name in dir(enum_obj):
                return name
        except Exception:
            pass

    return None


def _scene_text(scene: dict[str, Any]) -> str:
    """从分镜里抽出「画面主文案」。优先屏幕上的字，而不是口播稿。"""
    # 1) 场景级直接字段
    for key in ("word", "title", "text", "headline", "line"):
        val = scene.get(key)
        if val:
            return str(val)
    # 2) shots 内的画面文字（spec-chalk 结构：word / head / line）
    shots = scene.get("shots") or []
    for shot in shots:
        for key in ("word", "head", "text", "line"):
            val = shot.get(key)
            if val:
                return str(val)
    # 3) 兜底：口播稿（宁可有点字，也不要空白屏）
    for shot in shots:
        if shot.get("say"):
            return str(shot["say"])
    return scene.get("say") or ""


def _scene_duration(scene: dict[str, Any]) -> float:
    """没有显式 duration 时，按口播字数估算（中文约 4.2 字/秒），夹在 2~8s。"""
    if scene.get("duration"):
        return float(scene["duration"])
    shots = scene.get("shots") or []
    chars = sum(len(str(s.get("say") or "")) for s in shots if isinstance(s, dict))
    if not chars:
        return 3.0
    return max(2.0, min(8.0, chars / 4.2))


def build_timeline(spec: dict[str, Any], job_dir: Path,
                   say: Callable[[int, str], None]) -> dict[str, Any]:
    """spec → draft_id（VectCutAPI 内存对象）。"""
    scenes = spec.get("scenes") or []
    if not scenes:
        raise JianyingError("jianying spec has no scenes")

    width = int(spec.get("width") or 1920)
    height = int(spec.get("height") or 1080)
    created = _post("/create_draft", {"width": width, "height": height})
    draft_id = created["draft_id"]

    total = 0.0
    for idx, scene in enumerate(scenes):
        dur = _scene_duration(scene)
        text = _scene_text(scene)
        say(10 + int(60 * idx / max(1, len(scenes))), f"jianying:scene{idx + 1}/{len(scenes)}")
        if not text:
            total += dur
            continue

        native = scene.get("native") or {}
        payload: dict[str, Any] = {
            "draft_id": draft_id,
            "text": text,
            "start": total,
            "end": total + dur,
            "font_size": int(scene.get("font_size") or 12),
        }
        font = native.get("font")
        if font:
            fid = _asset_lookup(font, "fonts")
            if fid:
                payload["font"] = fid
        anim = native.get("text_anim_in")
        if anim:
            aid = _asset_lookup(anim, "text_animations") or anim
            # VectCutAPI 的参数名是 intro_animation（不是 text_animation）
            payload["intro_animation"] = aid
        _post("/add_text", payload)
        total += dur

    return {"draft_id": draft_id, "duration": total}


def render_jianying(payload: dict[str, Any], job_dir: Path,
                    progress: Callable[[int, str], None] | None = None) -> dict[str, Any]:
    """payload['spec'] 为分镜 JSON；返回 draftPath / draftName / duration。"""
    say = progress or (lambda p, m: None)
    spec = payload.get("spec") or {}
    # 平台侧既可能把分镜放在 spec.scenes，也可能包在 spec.way / spec.spec 里
    # （提交时 {"engine":"jianying","way":{...}} 这种结构）。
    if not spec.get("scenes"):
        for nest in ("way", "spec", "storyboard", "board"):
            inner = spec.get(nest)
            if isinstance(inner, dict) and inner.get("scenes"):
                spec = {**inner, "engine": spec.get("engine", "jianying")}
                break
    if not spec.get("scenes"):
        raise JianyingError("jianying spec has no scenes")

    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "spec.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    say(5, "jianying:ensure-service")
    ensure_service()

    say(10, "jianying:build")
    built = build_timeline(spec, job_dir, say)

    say(75, "jianying:save")
    name = payload.get("draftName") or f"studio_{job_dir.name[:20]}"
    saved = _post("/save_draft", {
        "draft_id": built["draft_id"],
        "project_name": name,
        "auto_deploy": False,
    }, timeout=900)

    src = Path(saved.get("draft_url") or "")
    if not src.exists():
        raise JianyingError(f"save_draft produced no folder: {saved}")

    say(90, "jianying:deploy")
    dest = _deploy(src, name)
    say(100, "jianying:done")
    return {
        "draftPath": str(dest),
        "draftName": name,
        "duration": built["duration"],
        "output": {"videoPath": None, "requiresManualExport": True},
        "note": "草稿已就绪。macOS 无法自动导出，请在剪映打开草稿并点导出。",
    }


if __name__ == "__main__":
    # 手工调试：python jianying_engine.py spec.json [draftName]
    spec_file = Path(sys.argv[1])
    draft_name = sys.argv[2] if len(sys.argv) > 2 else None
    spec_obj = json.loads(spec_file.read_text(encoding="utf-8"))
    out = render_jianying(
        {"spec": spec_obj, "draftName": draft_name},
        spec_file.parent / "_jy_job",
        lambda p, m: print(f"[{p:3d}%] {m}", file=sys.stderr))
    print(json.dumps(out, ensure_ascii=False))
