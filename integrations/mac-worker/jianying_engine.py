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
import time
import urllib.error
import urllib.request
import uuid
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

def _post(path: str, payload: dict[str, Any], timeout: int = 600,
          attempts: int = 3) -> dict[str, Any]:
    """调 VectCutAPI。瞬时故障重试三次，业务错（比如同轨叠加）立刻抛。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last: Exception | None = None
    for i in range(attempts):
        req = urllib.request.Request(
            f"{VCUT_BASE}{path}", data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
            if i + 1 < attempts:
                time.sleep(2 * (i + 1))
                continue
            raise JianyingError(
                f"VectCutAPI 连不上 {VCUT_BASE}（{exc}）。"
                "确认服务在跑：curl -s localhost:9100/get_transition_types | head -c 100"
            ) from exc
        if not body.get("success"):
            raise JianyingError(f"{path} failed: {body.get('error') or body}")
        return body.get("output") or {}
    raise JianyingError(f"{path} failed after {attempts} attempts: {last}")


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
    _normalize_draft(dest, project_name)
    _write_export_sidecar(dest, project_name)
    os.utime(dest, None)
    return dest


def _write_export_sidecar(dest: Path, project_name: str) -> dict[str, Any]:
    """把"这次草稿是什么规格"写到草稿目录，给导出环节当唯一可信来源。

    为什么需要：剪映 11.6 **保存后会把 draft_info.json 加密**（实测 draft_info.json
    首字节变成 `GGHbtvxhfjg...` 这种密文），所以导出时读不到画布尺寸/时长。
    我们自己的规格必须在写明文的那一刻就落盘。
    """
    side: dict[str, Any] = {"project": project_name, "engine": "jianying",
                            "written_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    info = dest / "draft_info.json"
    data = {}
    if info.exists():
        try:
            data = json.loads(info.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    cc = data.get("canvas_config") or {}
    side["canvas"] = {"width": cc.get("width"), "height": cc.get("height"),
                      "ratio": cc.get("ratio")}
    side["fps"] = data.get("fps") or data.get("frame_rate")
    side["duration_us"] = data.get("duration") or 0
    side["tracks"] = len(data.get("tracks") or [])
    side["materials"] = {k: len(v) for k, v in (data.get("materials") or {}).items()
                         if isinstance(v, list) and v}
    (dest / "studio_export_meta.json").write_text(
        json.dumps(side, ensure_ascii=False, indent=2), encoding="utf-8")
    return side


def _write_plain_json(path: Path, payload: str) -> bool:
    """写明文 JSON；若目标已是剪映自己写的密文则**拒绝覆盖**，返回 False。"""
    if path.exists():
        head = path.read_text(encoding="utf-8", errors="ignore")[:1]
        if head != "{":
            return False
    path.write_text(payload, encoding="utf-8")
    return True


def _normalize_draft(dest: Path, project_name: str) -> dict[str, Any]:
    """给草稿换一套独有 id，并把 meta 补成剪映列表能正确显示的样子。

    实测到的三个坑（2026-10-24，接管后排查）：
      0. **决定性**：剪映 11.6 的草稿注册表
         `<draft_root>/root_meta_info.json` 里每条记录的 `draft_json_file`
         **一律指向 `draft_info.json`**。11.6 只读这个文件；我们此前只写
         `draft_content.json`（历史文件名），所以时间线永远是空的。
         → 必须同时输出 `draft_info.json`（明文 JSON，与 10月24日 那个
         web 来源草稿同法，剪映读明文不吃加密）。
      1. 模板模式下 content["id"] 直接沿用模板草稿的 id，于是
         `studio_6adc23ad…` / `studio_6adc2526…` / `jy_native_demo` 三个草稿
         **共用同一个 timeline UUID `91E08AC5-…`**，
         `Timelines/<id>/`、`project.json`、`timeline_layout.json` 全部撞车。
      2. `draft_meta_info.json` 的 `tm_duration` / `draft_timeline_materials_size_`
         恒为 0，草稿列表里显示 0 秒。

    这里只在**部署之后**改落地文件，不依赖 VectCutAPI 内部实现。
    """
    content_path = dest / "draft_content.json"
    if not content_path.exists():
        return {"skipped": "no draft_content.json"}

    content = json.loads(content_path.read_text(encoding="utf-8"))
    old_id = content.get("id") or ""
    new_id = str(uuid.uuid4()).upper()
    duration = int(content.get("duration") or 0)

    # 1) content 本体 + draft_info.json（11.6 真正读取的文件）
    content["id"] = new_id
    payload = json.dumps(content, ensure_ascii=False, indent=2)
    content_path.write_text(payload, encoding="utf-8")
    wrote_info = _write_plain_json(dest / "draft_info.json", payload)

    # 2) Timelines/<id> 目录改名 + 里头的 content 副本
    timelines = dest / "Timelines"
    renamed: list[str] = []
    if timelines.is_dir():
        dirs = [p for p in timelines.iterdir() if p.is_dir()]
        target = timelines / new_id
        for td in dirs:
            if td == target:
                continue
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            td.rename(target)
            renamed.append(f"{td.name} -> {target.name}")
            break
        inner = target / "draft_content.json"
        if inner.exists():
            inner.write_text(payload, encoding="utf-8")
        _write_plain_json(target / "draft_info.json", payload)

        # 3) project.json / project.json.bak
        for name in ("project.json", "project.json.bak"):
            p = timelines / name
            if not p.exists():
                continue
            try:
                proj = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            proj["id"] = new_id
            proj["main_timeline_id"] = new_id
            for tl in proj.get("timelines") or []:
                tl["id"] = new_id
            p.write_text(json.dumps(proj, ensure_ascii=False, separators=(",", ":")),
                         encoding="utf-8")

    # 4) timeline_layout.json
    layout_path = dest / "timeline_layout.json"
    if layout_path.exists():
        try:
            layout = json.loads(layout_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            layout = None
        if layout:
            for item in layout.get("dockItems") or []:
                item["timelineIds"] = [new_id]
                item["timelineNames"] = ["时间线01"]
            layout_path.write_text(
                json.dumps(layout, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 5) meta 补全
    meta_path = dest / "draft_meta_info.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["draft_name"] = project_name
        meta["draft_fold_path"] = str(dest)
        meta["draft_id"] = new_id
        meta["tm_duration"] = duration
        meta["draft_timeline_materials_size_"] = os.path.getsize(content_path)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"old_id": old_id, "new_id": new_id, "duration": duration,
            "draft_info_written": wrote_info, "timeline_renamed": renamed}


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


MPT_HOME = Path(os.environ.get(
    "STUDIO_MPT_HOME",
    str(HOME / "CreatorStudio/runtime/integrations/vendor/MoneyPrinterTurbo"),
))
DEFAULT_VOICE = "sami:BV411_streaming"  # 剪映「解说小帅」，会员音色不经会员也能用


def _tts(text: str, out: Path, voice: str) -> None:
    """剪映音色配音（SAMI 内部接口）。

    worker 跑系统 python3，没有 MPT venv 的依赖（loguru/websockets），
    所以只能子进程交给 MPT 的解释器跑 sami_tts —— 与 slides_render 同一条桥。
    """
    python = MPT_HOME / ".venv/bin/python"
    script = MPT_HOME / "app/services/sami_tts.py"
    if not python.exists() or not script.exists():
        raise JianyingError(f"sami tts unavailable: {python} / {script}")
    code = (
        "import sys\n"
        "from app.services.sami_tts import sami_tts\n"
        "sami_tts(sys.argv[1], sys.argv[2], 1.0, sys.argv[3])\n"
    )
    env = dict(os.environ)
    env["MPT_HOME"] = str(MPT_HOME)
    env["PYTHONPATH"] = str(MPT_HOME)
    tail = "no output"
    for attempt in range(3):
        if out.exists():
            out.unlink()
        r = subprocess.run(
            [str(python), "-c", code, text, voice, str(out)],
            capture_output=True, text=True, cwd=str(MPT_HOME), env=env, timeout=240,
        )
        if out.exists() and out.stat().st_size >= 1000:
            return
        tail = (r.stderr or r.stdout or "no output").strip()[-300:]
        if attempt < 2:
            time.sleep(3 * (attempt + 1))
    raise JianyingError(f"tts failed ({voice}) 重试 3 次: {tail}")


def _dur(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


_CAT_ENDPOINT = {
    "transitions": "get_transition_types",
    "text_anim_in": "get_text_intro_types",
    "text_anim_out": "get_text_outro_types",
    "video_intro_animations": "get_intro_animation_types",
    "video_outro_animations": "get_outro_animation_types",
    "fonts": "get_font_types",
    "video_scene_effects": "get_video_scene_effect_types",
    "video_character_effects": "get_video_character_effect_types",
}
_CAT_CACHE: dict[str, list[str]] = {}


def _catalog(category: str) -> list[str]:
    """服务端的资源白名单（真值来源，不是本地 CSV）。

    踩过的坑：本地 asset_search 的 CSV 里有「黑板擦除」，而 VectCutAPI 内部的
    pyJianYingDraft 枚举只有「画笔擦除」，传中文名进去只会得到
    ``Warning: Unsupported transition type`` 然后被静默忽略。
    """
    if category in _CAT_CACHE:
        return _CAT_CACHE[category]
    endpoint = _CAT_ENDPOINT.get(category)
    names: list[str] = []
    if endpoint:
        try:
            with urllib.request.urlopen(f"{VCUT_BASE}/{endpoint}", timeout=60) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            names = [str(x.get("name")) for x in (body.get("output") or []) if x.get("name")]
        except Exception:  # noqa: BLE001 - 服务异常时退化为透传，不阻断整条渲染
            names = []
    _CAT_CACHE[category] = names
    return names


def _pick(name: Any, category: str) -> str | None:
    """资源名 → 服务端认的标识。精确 → 模糊 → 放弃（绝不硬塞中文名）。"""
    if not name:
        return None
    raw = str(name).strip()
    if not raw:
        return None
    names = _catalog(category)
    if not names:
        return raw  # 拿不到白名单，只能透传交给服务端判定
    if raw in names:
        return raw
    contains = [n for n in names if raw in n or n in raw]
    if contains:
        return min(contains, key=len)
    return None


def _as_list(value: Any) -> list[Any]:
    if not value:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def build_timeline(spec: dict[str, Any], job_dir: Path,
                   say: Callable[[int, str], None]) -> dict[str, Any]:
    """spec → {draft_id, duration, stats}。四层轨道：画面 / 文字 / 特效 / 配音。

    全部走剪映原生资源（转场、画面特效、VIP 字体、文字入场动画、音色），
    自研渲染器追不上这部分，也正是会员的价值所在。
    """
    if spec.get("template") == "factcard" and not spec.get("scenes"):
        # 平台只提交选题与要点，卡面在 worker 本地出图（版式实现只维护一份）
        from factcard import expand as _expand_factcard

        spec = _expand_factcard(spec, job_dir)
        say(8, "干货卡版式已展开")
    scenes = spec.get("scenes") or []
    if not scenes:
        raise JianyingError("jianying spec has no scenes")

    width = int(spec.get("width") or 1920)
    height = int(spec.get("height") or 1080)
    created = _post("/create_draft", {"width": width, "height": height})
    draft_id = created["draft_id"]

    # 第一遍：先拿到配音，才知道每屏真实时长（按口播字数估的和实际差很多）
    plan: list[dict[str, Any]] = []
    for idx, scene in enumerate(scenes):
        native = scene.get("native") or {}
        dur = _scene_duration(scene)
        voice = scene.get("voice") or native.get("voice")
        audio: Path | None = None
        if voice:
            text_for_tts = str(scene.get("say") or "").strip() or _scene_text(scene)
            if text_for_tts:
                say(5 + int(30 * idx / max(1, len(scenes))),
                    f"jianying:tts{idx + 1}/{len(scenes)}")
                audio = job_dir / f"tts_{idx:02d}.mp3"
                _tts(text_for_tts, audio, str(voice))
                dur = max(dur, _dur(audio) + 0.25)
        plan.append({"scene": scene, "native": native, "duration": dur, "audio": audio})

    total = 0.0
    stats = {"images": 0, "texts": 0, "effects": 0, "audios": 0,
             "transitions": 0, "text_anims": 0, "fonts": 0, "keyframes": 0,
             "characters": 0, "skipped": []}
    for idx, item in enumerate(plan):
        scene, native = item["scene"], item["native"]
        dur = item["duration"]
        say(35 + int(55 * idx / max(1, len(plan))),
            f"jianying:scene{idx + 1}/{len(plan)}")

        # ---- 1) 画面轨：图片片段（转场只能挂在片段上，挂不到文字上）
        image = scene.get("image") or native.get("image")
        if image:
            img = Path(str(image)).expanduser()
            if img.exists():
                clip: dict[str, Any] = {
                    "draft_id": draft_id,
                    "image_url": str(img),
                    "width": width,
                    "height": height,
                    "start": total,
                    "end": total + dur,
                }
                transition = _pick(native.get("transition_in"), "transitions")
                if transition:
                    clip["transition"] = transition
                    clip["transition_duration"] = float(
                        native.get("transition_duration") or 0.6)
                    stats["transitions"] += 1
                anim_in = _pick(native.get("image_anim_in"), "video_intro_animations")
                if anim_in:
                    clip["intro_animation"] = anim_in
                    clip["intro_animation_duration"] = float(
                        native.get("image_anim_in_duration") or 0.6)
                anim_out = _pick(native.get("image_anim_out"), "video_outro_animations")
                if anim_out:
                    clip["outro_animation"] = anim_out
                    clip["outro_animation_duration"] = float(
                        native.get("image_anim_out_duration") or 0.5)
                _post("/add_image", clip)
                stats["images"] += 1

                # 缓慢推/拉（Ken Burns）：静态卡最容易显得"PPT"，
                # 关键帧在 VectCutAPI 里按绝对时间命中片段，所以这里用本屏的起止。
                kb = native.get("kenburns")
                if kb is None:
                    kb = spec.get("template") == "factcard"
                if kb:
                    zoom_in = idx % 2 == 0
                    frm = float(native.get("kenburns_from") or (1.0 if zoom_in else 1.06))
                    to = float(native.get("kenburns_to") or (1.06 if zoom_in else 1.0))
                    t0 = total + 0.1
                    t1 = max(t0 + 0.4, total + dur - 0.1)
                    try:
                        _post("/add_video_keyframe", {
                            "draft_id": draft_id,
                            "track_name": str(clip.get("track_name") or "image_main"),
                            "property_types": ["uniform_scale", "uniform_scale"],
                            "times": [round(t0, 3), round(t1, 3)],
                            "values": [f"{frm:.3f}", f"{to:.3f}"],
                        })
                        stats["keyframes"] += 1
                    except Exception as exc:  # noqa: BLE001 - 关键帧失败不该毁掉整条片
                        stats["skipped"].append(f"keyframe: {exc}")

        # ---- 1.5) 角色轨：鲸鱼娘常驻右下，跟这屏口播一起呼吸
        char_cfg = _character_config(spec)
        if char_cfg is not None:
            track = str(char_cfg.get("track") or "character")
            try:
                png = _character_frame(char_cfg, idx, len(plan), job_dir, width, height,
                                       bool(item["audio"]))
            except Exception as exc:  # noqa: BLE001 - 角色贴不上不该毁掉整条片
                png = None
                stats["skipped"].append(f"character: {exc}")
            if png:
                cclip: dict[str, Any] = {
                    "draft_id": draft_id,
                    "image_url": str(png),
                    "width": width,
                    "height": height,
                    "start": total,
                    "end": total + dur,
                    "track_name": track,
                    "relative_index": 1,
                }
                anim_in = _pick(char_cfg.get("anim_in") or "渐显",
                                "video_intro_animations")
                if anim_in:
                    cclip["intro_animation"] = anim_in
                    cclip["intro_animation_duration"] = float(
                        char_cfg.get("anim_in_duration") or 0.5)
                try:
                    _post("/add_image", cclip)
                    stats["characters"] += 1
                except Exception as exc:  # noqa: BLE001
                    stats["skipped"].append(f"character_clip: {exc}")

                # 呼吸 + 轻微起伏：position_y 单位是半个画布高，0.008≈8px；
                # 属性名必须用 pyJianYingDraft 的枚举名（transform_y 会被服务端拒绝）。
                amp = float(char_cfg.get("bob") or 0.008)
                breath = float(char_cfg.get("breath") or 1.015)
                t0 = total + 0.2
                mid = total + dur / 2
                t1 = max(mid + 0.3, total + dur - 0.2)
                sway = float(char_cfg.get("sway") or 1.2)
                for prop, lo, hi in (("position_y", "0", f"{amp:.4f}"),
                                     ("uniform_scale", "1", f"{breath:.3f}"),
                                     ("rotation", "0", f"{sway:.2f}")):
                    try:
                        _post("/add_video_keyframe", {
                            "draft_id": draft_id,
                            "track_name": track,
                            "property_types": [prop, prop, prop],
                            "times": [round(t0, 3), round(mid, 3), round(t1, 3)],
                            "values": [lo, hi, lo],
                        })
                        stats["keyframes"] += 1
                    except Exception as exc:  # noqa: BLE001
                        stats["skipped"].append(f"char_keyframe({prop}): {exc}")

        # ---- 2) 文字轨：主文案（VIP 字体 + 入场/出场动画）+ 可选逐句字幕
        text = _scene_text(scene)
        caption = str(scene.get("caption") or native.get("subtitle") or "").strip()
        blocks: list[dict[str, Any]] = []
        if text:
            body: dict[str, Any] = {
                "text": text,
                "font_size": int(native.get("font_size") or scene.get("font_size") or 12),
                "transform_y": float(native.get("transform_y", scene.get("transform_y", -0.45))),
                "transform_x": float(native.get("transform_x", 0.0)),
            }
            font = _pick(native.get("font"), "fonts")
            if font:
                body["font"] = font
                stats["fonts"] += 1
            intro = _pick(native.get("text_anim_in"), "text_anim_in")
            if intro:
                # 注意：VectCutAPI 的参数名是 intro_animation，不是 text_animation
                body["intro_animation"] = intro
                body["intro_duration"] = float(native.get("text_anim_in_duration") or 0.5)
                stats["text_anims"] += 1
            outro = _pick(native.get("text_anim_out"), "text_anim_out")
            if outro:
                body["outro_animation"] = outro
                body["outro_duration"] = float(native.get("text_anim_out_duration") or 0.5)
                stats["text_anims"] += 1
            if native.get("color"):
                body["font_color"] = str(native["color"])
            if native.get("bold"):
                body["bold"] = True
            if native.get("width"):
                body["width"] = float(native["width"])
            body["track_name"] = str(native.get("text_track") or "text_main")
            blocks.append(body)
        if caption and caption != text:
            # 竖屏字幕必须落进底部安全带：half-canvas 单位，y=-0.86 ≈ 画面 93% 处
            blocks.append({
                "text": caption,
                "font_size": int(native.get("caption_size") or 7),
                "font_color": str(native.get("caption_color") or "#FFFFFF"),
                "transform_y": float(native.get("caption_y", -0.86)),
                "width": float(native.get("caption_width", 0.88)),
                # 不加这句会落到 pyJianYingDraft 默认的「文轩体」，字幕观感太软
                "font": str(native.get("caption_font") or "SourceHanSansCN_Medium"),
                "bold": True,
                # 同轨同时长的片段会被 pyJianYingDraft 判为 overlap，字幕必须独立轨
                "track_name": str(native.get("caption_track") or "text_sub"),
            })
        for body in blocks:
            payload = {"draft_id": draft_id, "start": total, "end": total + dur, **body}
            _post("/add_text", payload)
            stats["texts"] += 1

        # ---- 3) 画面特效轨（转场之外的氛围层）
        for eff in _as_list(native.get("effect")):
            effect_type = _pick(eff, "video_scene_effects")
            if not effect_type:
                # 静默丢弃最坑：目录里没有的名字要能在 stats 里看见
                stats["skipped"].append(f"effect: {eff}")
                continue
            _post("/add_effect", {
                "draft_id": draft_id,
                "effect_type": effect_type,
                "effect_category": str(native.get("effect_category") or "scene"),
                "start": total,
                "end": total + dur,
                "width": width,
                "height": height,
            })
            stats["effects"] += 1

        # ---- 4) 配音轨（剪映音色）
        audio = item["audio"]
        if audio:
            _post("/add_audio", {
                "draft_id": draft_id,
                "audio_url": str(audio),
                "target_start": total,
                "volume": float(native.get("volume") or 1.0),
            })
            stats["audios"] += 1

        total += dur

    return {"draft_id": draft_id, "duration": total, "stats": stats}


_CHARACTERS_ROOT = Path("~/CreatorStudio/state/characters").expanduser()
_CHAR_FRAME_CACHE: dict[tuple[str, str], Any] = {}


def _character_config(spec: dict[str, Any]) -> dict[str, Any] | None:
    """spec.character 决定角色层：不加就不贴，加上就常驻右下角。"""
    cfg = spec.get("character")
    if cfg is False:
        return None
    if cfg is None:
        # 默认只有分镜卡走角色层，其他引擎的 spec 不该被这条路影响
        if spec.get("template") != "factcard":
            return None
        cfg = {}
    if not isinstance(cfg, dict) or cfg.get("enabled") is False:
        return None
    return cfg


def _character_pose(cfg: dict[str, Any], idx: int, total: int) -> str:
    poses = cfg.get("poses")
    if isinstance(poses, list) and poses:
        return str(poses[idx % len(poses)])
    if idx == 0:
        return "wave"       # 开场挥手，先把人立住
    if idx >= total - 1:
        return "happy"      # 收尾给情绪高点
    return "point" if idx % 2 else "think"


def _character_frame(cfg: dict[str, Any], idx: int, total: int, job_dir: Path,
                     width: int, height: int, speaking: bool) -> Path | None:
    """把立绘贴到一张透明画布上，交回剪映当独立图层（可单独做动效）。"""
    from PIL import Image

    import character

    pose = str(cfg.get("pose") or _character_pose(cfg, idx, total))
    # 口型按镜头轮换：全片一个 "half" 会让嘴像被冻住
    _MOUTHS = ("open", "half", "open", "round")
    mouth = _MOUTHS[idx % len(_MOUTHS)] if speaking else "closed"
    key = (pose, mouth)
    figure = _CHAR_FRAME_CACHE.get(key)
    if figure is None:
        figure = character.render(pose=pose, mouth=mouth)
        _CHAR_FRAME_CACHE[key] = figure
    w = int(float(cfg.get("width") or 560) * width / 1080)
    fig = figure.resize((w, w), Image.LANCZOS)
    # 立绘四周有透明留白：按内容框对齐，否则右边缘会把鲸尾切掉
    bb = fig.getbbox() or (0, 0, w, w)
    cw, chh = bb[2] - bb[0], bb[3] - bb[1]
    x = int(float(cfg.get("x") or 650) * width / 1080)
    y = int(float(cfg.get("y") or 1070) * height / 1920)
    x = max(0, min(x, width - 32 - cw))
    y = max(0, min(y, height - chh))
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.alpha_composite(fig, (x - bb[0], y - bb[1]))
    out = job_dir / f"char_{idx:02d}.png"
    canvas.save(out)
    return out


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
    if not spec.get("scenes") and spec.get("template") == "factcard":
        # 干货卡模板：选题 + 要点 → worker 本地出卡面 → 展开成 scenes
        job_dir.mkdir(parents=True, exist_ok=True)
        from factcard import expand as _expand_factcard

        spec = _expand_factcard(spec, job_dir)
        say(8, "factcard:expanded")
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
        "stats": built.get("stats") or {},
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
