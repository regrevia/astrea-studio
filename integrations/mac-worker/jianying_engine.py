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

import hashlib
import json
import os
import re
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


_CAPTION_BREAK = "，。、；：！？,.;:!?"


def _caption_chunks(text: str, limit: int = 16) -> list[str]:
    """把一句话切成能一眼读完的字幕块。

    句号是硬边界（念完就是一句），逗号顿号是软边界（同一句里的换气），
    所以在句内合并、跨句不合并；行尾不留标点。
    """
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return []
    # 中文之间的空格是噪音；中文与西文之间留一个，别让 "KV Cache" 粘成 "KVCache"
    raw = re.sub(r"(?<=[\u4e00-\u9fff]) (?=[\u4e00-\u9fff])", "", raw)
    raw = re.sub(r"(?<=[A-Za-z0-9])(?=[\u4e00-\u9fff])", " ", raw)
    raw = re.sub(r"(?<=[\u4e00-\u9fff])(?=[A-Za-z0-9])", " ", raw)
    soft = "，、；：,.;:"
    chunks: list[str] = []
    for sent in re.findall(r"[^。！？!?]+[。！？!?]?", raw):
        clauses: list[str] = []
        buf = ""
        for ch in sent:
            buf += ch
            if ch in soft:
                clauses.append(buf)
                buf = ""
        if buf:
            clauses.append(buf)
        cur = ""
        for cl in clauses:
            if cur and len(cur) + len(cl) > limit:
                chunks.append(cur)
                cur = cl
            else:
                cur += cl
        if cur:
            chunks.append(cur)
    # 没有逗号的长句只能硬切：竖屏字幕一屏一行，超了就会自己折成两行
    split: list[str] = []
    for c in chunks:
        while len(c) > limit:
            cut = limit
            for i in range(limit, max(4, limit - 6), -1):
                if c[i - 1] in "的了是和与就都也很":
                    cut = i
                    break
            split.append(c[:cut])
            c = c[cut:]
        split.append(c)
    out = []
    for c in split:
        c = c.strip().rstrip("，。、；：,.;:")
        if not c:
            continue
        # 中文紧贴英文会糊成一坨："KVCache不是" / "prefill把缓存" 都要补空格
        c = re.sub(r"(?<=[A-Za-z0-9])(?=[\u4e00-\u9fff])", " ", c)
        c = re.sub(r"(?<=[\u4e00-\u9fff])(?=[A-Za-z0-9])", " ", c)
        out.append(c)
    return out


def _caption_spans(chunks: list[str], start: float, dur: float) -> list[tuple[float, float]]:
    """按字数分配时长：每块至少 1.1s，末块收到画面末尾。"""
    # 下限 6 字：像「先说结论」这种 4 字块不该只闪 0.7 秒
    weights = [max(6, len(c)) for c in chunks]
    total_w = float(sum(weights)) or 1.0
    spans: list[tuple[float, float]] = []
    acc = start
    for i, w in enumerate(weights):
        seg = dur * w / total_w
        end = start + dur if i == len(chunks) - 1 else acc + seg
        spans.append((round(acc, 3), round(end, 3)))
        acc += seg
    return spans


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


TTS_CACHE = Path(os.path.expanduser("~/CreatorStudio/cache/tts"))


def _tts_cache_path(text: str, voice: str) -> Path | None:
    """配音缓存键：音色 + 原文。稿子改一个字就重新合成，不改就复用。"""
    key = hashlib.sha1(f"{voice}\x00{text}".encode()).hexdigest()[:20]
    slug = re.sub(r"[^0-9A-Za-z]+", "-", voice).strip("-") or "voice"
    return TTS_CACHE / slug / f"{key}.mp3"


TTS_CACHE_MAX_BYTES = 1200 * 1024 * 1024   # 缓存上限：一只 20 秒片约 200KB，够存几千条
TTS_CACHE_MAX_AGE = 45 * 86400              # 45 天没被用到的音色旧稿子没人会再出片


def _prune_tts_cache(keep: Path | None = None) -> None:
    """配音缓存要封顶：一直只写不删，跑几个月磁盘就满了，平台跟着躺。

    先按年龄删，再按总量从最久没用的删起；刚写进去的那条永远留着。
    清理失败不影响出片，缓存是加速用的，不是产物。
    """
    try:
        if not TTS_CACHE.exists():
            return
        now = time.time()
        files = []
        for f in TTS_CACHE.glob("*/*.mp3"):
            try:
                st = f.stat()
            except OSError:
                continue
            if keep is not None and f == keep:
                continue
            if now - st.st_mtime > TTS_CACHE_MAX_AGE:
                try:
                    f.unlink()
                except OSError:
                    pass
                continue
            files.append((st.st_mtime, st.st_size, f))
        total = sum(sz for _m, sz, _f in files)
        if total <= TTS_CACHE_MAX_BYTES:
            return
        for _m, sz, f in sorted(files):
            if total <= TTS_CACHE_MAX_BYTES:
                break
            try:
                f.unlink()
                total -= sz
            except OSError:
                pass
    except Exception:
        pass


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
    # 同音色同文本只合成一次：SAMI 流式音色每次采样都有细微差别，
    # 不缓存的话同一份稿子两次出片口型、时长两样，批量出片没法对比母版。
    cache = _tts_cache_path(text, voice)
    if cache is not None and cache.exists() and cache.stat().st_size >= 1000:
        shutil.copyfile(cache, out)
        return
    tail = "no output"
    for attempt in range(3):
        if out.exists():
            out.unlink()
        r = subprocess.run(
            [str(python), "-c", code, text, voice, str(out)],
            capture_output=True, text=True, cwd=str(MPT_HOME), env=env, timeout=240,
        )
        if out.exists() and out.stat().st_size >= 1000:
            if cache is not None:
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(out, cache)
                    _prune_tts_cache(cache)
                except OSError:
                    pass
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
                # 有配音时以配音为准：画面比声音长就变成「话讲完了画面还杵着」，
                # 每屏多停 0.3~1.3 秒，整片就出 PPT 感。只留一点收尾气口。
                dur = max(1.6, min(9.0, _dur(audio) + 0.18))
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
                # 末屏后面没有画面，转场只会变成片尾淡黑，吃掉收尾卡的阅读时间
                if transition and idx < len(plan) - 1:
                    td = float(native.get("transition_duration") or 0.6)
                    # 短屏用长转场，一半时间都在花掉，正文来不及看
                    if dur < 2.6:
                        td = min(td, 0.45)
                    clip["transition"] = transition
                    clip["transition_duration"] = td
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
            scene = item.get("scene") or {}
            pose = str(char_cfg.get("pose") or _scene_pose(char_cfg, idx, len(plan), scene))
            alt_pose = (None if char_cfg.get("pose") else
                        _second_half_pose(char_cfg, idx, len(plan), scene, first=pose))
            speaking = bool(item["audio"])
            # 说话就按拍点切成几段不同口型，一屏一个定格看着像贴图
            beats: list[tuple[str, float, float]] = []
            if speaking and char_cfg.get("lip_flap", True):
                period = float(char_cfg.get("lip_period") or 0.42)
                count = max(2, min(8, int(round(dur / period)) or 2))
                seg = dur / count
                for k in range(count):
                    end = total + dur if k == count - 1 else total + seg * (k + 1)
                    beats.append((("open", "half", "round", "half")[k % 4],
                                  total + seg * k, end))
            if not beats:
                beats = [("", total, total + dur)]

            clips = 0
            switched = False
            for k, (mouth, cstart, cend) in enumerate(beats):
                try:
                    use_pose = pose
                    if (alt_pose and char_cfg.get("gesture_sync", True)
                            and cstart >= total + dur * 0.55):
                        use_pose = alt_pose
                    png = _character_frame(char_cfg, idx, len(plan), job_dir, width, height,
                                           speaking, scene, use_pose, mouth)
                except Exception as exc:  # noqa: BLE001 - 角色贴不上不该毁掉整条片
                    stats["skipped"].append(f"character: {exc}")
                    continue
                if not png:
                    continue
                cclip: dict[str, Any] = {
                    "draft_id": draft_id,
                    "image_url": str(png),
                    "width": width,
                    "height": height,
                    "start": round(cstart, 3),
                    "end": round(cend, 3),
                    "track_name": track,
                    "relative_index": 1,
                }
                # 换手势那一拍给个短促放大：硬切容易被看成卡帧，给一点动作起势。
                # 只在切换那一拍加，后面持续同姿态还加就是每 0.4 秒抖一下。
                if use_pose != pose and not switched:
                    pop = _pick("轻微放大", "video_intro_animations")
                    if pop:
                        cclip["intro_animation"] = pop
                        cclip["intro_animation_duration"] = 0.3
                        switched = True
                # 只在全片第一次出场时渐显。每屏都渐显 = 六次闪现，
                # 角色像贴纸一样反复贴上取下；跨屏连续在场才像"人一直在"。
                if k == 0 and idx == 0:
                    anim_in = _pick(char_cfg.get("anim_in") or "渐显",
                                    "video_intro_animations")
                    if anim_in:
                        cclip["intro_animation"] = anim_in
                        cclip["intro_animation_duration"] = float(
                            char_cfg.get("anim_in_duration") or 0.5)
                if k == len(beats) - 1 and idx == len(plan) - 1:
                    anim_out = _pick(char_cfg.get("anim_out") or "渐隐",
                                     "video_outro_animations")
                    if anim_out:
                        cclip["outro_animation"] = anim_out
                        cclip["outro_animation_duration"] = float(
                            char_cfg.get("anim_out_duration") or 0.6)
                try:
                    _post("/add_image", cclip)
                    clips += 1
                except Exception as exc:  # noqa: BLE001
                    stats["skipped"].append(f"character_clip: {exc}")
            stats["characters"] += clips
            stats.setdefault("poses", []).append(
                f"{idx}:{pose}" + (f"→{alt_pose}" if alt_pose else ""))
            if clips:
                # 呼吸 + 轻微起伏：position_y 单位是半个画布高，0.008≈8px；
                # 属性名必须用 pyJianYingDraft 的枚举名（transform_y 会被服务端拒绝）。
                bob_k, sway_k = _MOTION.get(pose, (1.0, 1.0))
                amp = float(char_cfg.get("bob") or 0.008) * bob_k
                base = float(_POSE_SCALE.get(pose, 1.0))
                breath = base + (float(char_cfg.get("breath") or 1.015) - 1.0) * bob_k
                t0 = total + 0.2
                mid = total + dur / 2
                t1 = max(mid + 0.3, total + dur - 0.2)
                sway = float(char_cfg.get("sway") or 1.2) * sway_k
                for prop, lo, hi in (("position_y", "0", f"{amp:.4f}"),
                                     ("uniform_scale", f"{base:.3f}", f"{breath:.3f}"),
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
        # no_text：正文已经烧进卡面，再叠一轨就是重影（收尾卡就是这样）
        text = "" if scene.get("no_text") else _scene_text(scene)
        # 字幕要跟着配音走：写死的卡面摘要会和耳朵听到的对不上
        caption = str(scene.get("caption") or native.get("subtitle")
                      or scene.get("say") or "").strip()
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
            # 两行标题用默认行距（0.25）会散成两段，标题要压紧成一整块
            if native.get("line_spacing") is not None:
                body["line_spacing"] = float(native["line_spacing"])
            if native.get("width"):
                body["width"] = float(native["width"])
            body["track_name"] = str(native.get("text_track") or "text_main")
            blocks.append(body)
        chunks = [c for c in _caption_chunks(caption, int(native.get("caption_chunk") or 16))
                  if c != text]
        if chunks:
            # 竖屏字幕必须落进底部安全带：half-canvas 单位，y=-0.86 ≈ 画面 93% 处
            base = {
                "font_size": int(native.get("caption_size") or 7),
                "font_color": str(native.get("caption_color") or "#FFFFFF"),
                "transform_y": float(native.get("caption_y", -0.86)),
                "width": float(native.get("caption_width", 0.88)),
                # 不加这句会落到 pyJianYingDraft 默认的「文轩体」，字幕观感太软
                "font": str(native.get("caption_font") or "SourceHanSansCN_Medium"),
                "bold": True,
                # 同轨同时长的片段会被 pyJianYingDraft 判为 overlap，字幕必须独立轨
                "track_name": str(native.get("caption_track") or "text_sub"),
            }
            for chunk, span in zip(chunks, _caption_spans(chunks, total, dur)):
                blocks.append({"text": chunk, "_span": span, **base})
        for body in blocks:
            span = body.pop("_span", None) or (total, total + dur)
            payload = {"draft_id": draft_id, "start": span[0], "end": span[1], **body}
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
    if cfg is True:
        return {}
    if not isinstance(cfg, dict) or cfg.get("enabled") is False:
        return None
    return cfg


def _third_pose(cfg: dict[str, Any], idx: int, total: int,
                scene: dict[str, Any], first: str, second: str) -> str | None:
    """一句话够长再换第三次手势，动作仍然只从这句话命中过的里面挑。

    一屏只换一次，四秒多的时间里身体是不动的；观众看到的就是「一张图在说话」。
    第三次不引入任何新语义：分数为正、且不是前两个用过的，就都算这句话说得通。
    """
    say = str((scene or {}).get("say") or "").strip()
    # 以前要 18 字才给第三次换势：一屏四秒里身体后半截定住，看着就是贴图。
    # 12 字已经够两个动作讲完一句话，门槛降到跟换势逻辑一致。
    if len(say) < 12:
        return None
    for name, score in sorted(_keyword_scores(say).items(), key=lambda kv: -kv[1]):
        if name in (first, second) or float(score) <= 0:
            continue
        return str(name)
    return None


def _clause_wants(say: str) -> tuple[str, str]:
    """一句话按句读劈开，两半各自命中的姿态；断点跟换势共用 _clause_cut。"""
    text = str(say or "").strip()
    cut = _clause_cut(text)
    if cut < 0:
        return "", ""
    return _keyword_pose(text[:cut]), _keyword_pose(text[cut:])


def _second_half_pose(cfg: dict[str, Any], idx: int, total: int,
                      scene: dict[str, Any], first: str = "",
                      seed: int = 0) -> str | None:
    """一句话里换了话题就换手势：前半句思考、后半句给结论，比整屏一个姿势像在演。

    只在句读后还剩下实质内容时切换，短句硬切反而像卡顿。
    """
    say = str((scene or {}).get("say") or "").strip()
    if len(say) < 12:      # 再短就只剩一个动作，切了像抖
        return None
    cut = _clause_cut(say)
    if cut < 0:
        return None
    tail = say[cut:]
    head_say = say[:cut]
    tail_want = _keyword_pose(tail)
    head_want = _keyword_pose(head_say)
    base_want = _keyword_pose(say)
    # 只有两半句的语义真的不同才换：后半句在说另一件事，动作跟着变；
    # 整句一个意思时换来换去只是抽搐。
    # first 是前半句真正演的那个姿势：它可能因为「别跟前两屏撞」被换过，
    # 所以换势要躲开 first，而不是躲开整句的 want。
    if tail_want and tail_want != base_want and tail_want != first:
        return tail_want
    # 后半句没有自己的关键词时，不能就此定格一整屏。从这句话本身已经
    # 命中过的动作里挑一个分数接近、但和前半句不同的换上去：两种动作都有
    # 文本依据，体检也不会判成错配，看起来才像一句话里换了个手势。
    scores = _keyword_scores(say)
    top = float(scores.get(base_want) or 0.0)
    if top <= 0:
        return None
    # 只在「讲解类」动作里换：开心/挥手是情绪和礼数，一句批评话里换成笑脸就假了
    cands = [
        name for name, score in sorted(scores.items(), key=lambda kv: -kv[1])
        # 只躲「前半句正在演的那个」：整句 want 本身也是候选，前半句让位演了
        # 次优线索时，后半句回到整句的重心，正好是一句话里的态度转折。
        if name != first and name not in ("happy", "wave")
        and float(score) > 0 and float(score) >= top * 0.55
    ]
    return _variant_pick(cands, seed) or None


def _clause_cut(say: str) -> int:
    """句子在哪断开。手势换势和「后半句动作」必须用同一个断点，否则动作和话错位。

    取正文 25%–75% 之间最靠近中间的那个断句符（返回断点后的下标）。
    """
    text = str(say or "")
    n = len(text)
    if n < 8:
        return -1
    best, best_d = -1, 9.9
    for i, ch in enumerate(text):
        if ch not in "，,；;。.！!？?：:、":
            continue
        cut = i + 1
        if cut < 3 or cut > n - 3:
            continue
        frac = cut / n
        if not 0.25 <= frac <= 0.75:
            continue
        d = abs(frac - 0.5)
        if d < best_d:
            best, best_d = cut, d
    return best


# 讲述内容 → 姿态。角色得跟着这句话在做什么，而不是按屏号轮流摆姿势。
_POSE_RULES = (
    # 说话内容 → 动作：手要跟着这句话在做的事，而不是跟着句号。
    ("shrug", ("不编", "未覆盖", "读不到", "看不见", "没法", "做不到",
               "缺口", "漏掉", "抱歉", "代价", "成本", "占卡", "吃显存",
               "越占", "换速度", "不够", "吃紧", "卡住", "爆", "扛不住",
               "撑不住")),
    ("think", ("？", "?", "为什么", "怎么", "是不是", "到底", "吗",
               "只读到", "只读了", "差不多", "想想", "凭", "取决于", "不一定", "假设",
               "猜", "原理")),
    ("point", ("抽帧", "喂给", "挂上", "跳过去", "引用", "记住", "第一条", "时间戳", "取回", "静默",
               "第二步", "第三步", "关键", "注意", "一定要", "重点",
               "因为", "所以", "先跑", "铺好", "这一步", "分两步")),
    ("happy", ("真的", "太", "最", "居然", "竟然", "惊喜", "爽", "好用",
               "拿下", "搞定", "省时间", "省显存", "更快", "划算")),
    # 新立绘多出来四个表情：惊、鼓、怯、赞。只在句子里真有这层意思时才用，
    # 不然就是给每句话硬配表情包。
    ("surprise", ("没想到", "原来", "才发现", "突然", "一下子", "真没想到",
                  "开眼", "翻车", "这么夸张", "全军覆没")),
    ("pout", ("全挂", "挂了", "白挂")),
    ("pout", ("白等", "吃亏", "不划算", "折腾", "难受", "背锅", "凭什么",
              "懒得", "又是", "反复", "零弹窗", "靠脑补")),
    ("shy", ("不好意思", "抱歉", "小声", "偷偷", "害羞", "其实我",
             "说句实话", "说实话")),
    ("approve", ("没错", "就是这样", "就这么查", "稳妥", "靠谱", "信得过",
                 "照做", "对得上", "只认", "照实", "核对", "别信我", "自己看",
                 "查一遍", "查一下", "对一下")),
)
# 每个姿态的动幅：(上下起伏倍数, 摆动倍数)。想事情就该比挥手安静。
_MOTION = {"idle": (1.0, 1.0), "wave": (1.3, 1.8), "point": (0.9, 0.7),
           "think": (0.6, 0.5), "happy": (1.5, 2.0), "shrug": (0.7, 0.6),
           "surprise": (1.2, 1.1), "pout": (0.8, 0.9),
           "shy": (0.7, 0.7), "approve": (1.1, 1.0)}
# 姿态还该带一点体型差别：兴奋时凑近，思考时退半步。只改关键帧倍数，
# 立绘本身不重画，帧缓存还能复用。
_POSE_SCALE = {"happy": 1.06, "wave": 1.04, "point": 1.02, "think": 0.98,
               "surprise": 1.05, "shy": 0.99, "approve": 1.03}


# 一句话常常同时命中好几种线索（「为什么这一步一定要抽帧」里有疑问也有动作）。
# 按规则顺序取第一个，等于让「忍不住想问」压过「正在指」，演出来的动作跟句子重心错开。
# 改成打分：词越长越具体分越高，同分再按规则顺序。
# 说「我没覆盖到」是比发问更重的动作：观众记住的是坦白，不是问句
_POSE_BASE = {"shrug": 1.30, "think": 1.10, "point": 1.0, "happy": 0.95,
              "surprise": 1.15, "pout": 1.05, "shy": 0.95, "approve": 0.9}


def _keyword_scores(text: str) -> dict[str, float]:
    scores: dict[str, float] = {}
    for pose, keys in _POSE_RULES:
        for key in keys:
            if key and key in text:
                # 长词是更具体的线索：「为什么」比「吗」指向更明确
                scores[pose] = scores.get(pose, 0.0) + _POSE_BASE.get(pose, 1.0) * (0.6 + 0.12 * len(key))
    return scores


def _keyword_pose(text: str) -> str:
    """只看这句话在讲什么，返回对应姿态；没有线索返回空串。"""
    scores = _keyword_scores(text)
    if not scores:
        return ""
    order = {pose: i for i, (pose, _) in enumerate(_POSE_RULES)}
    # 分高者胜，同分按规则顺序（缺口 > 疑问 > 动作 > 情绪）
    return max(scores, key=lambda pose: (round(scores[pose], 3), -order.get(pose, 9)))


def _variant_pick(cands: list[str], seed: int) -> str:
    """同一份稿子多出几条母版时，换一个「也在语义范围内」的动作。

    候选只能来自这句话原本命中的线索，所以换的是表演，不是内容：文案一个字
    没动，体检也不会判成错配。seed 为 0 时保持原来的首选，老片复现不变。
    """
    if not seed or len(cands) < 2:
        return cands[0] if cands else ""
    return cands[seed % len(cands)]


def _scene_pose(cfg: dict[str, Any], idx: int, total: int,
                scene: dict[str, Any], avoid: str = "",
                avoid_extra: str = "", seed: int = 0) -> str:
    """场景自带 pose 最优先，其次按讲述内容挑，最后才按首尾/奇偶兜底。

    avoid 是上一屏已经用过的姿势：连着三屏都在"想事情"，观众看到的是定格的
    表情包。同样的语义分之下换一个次优线索，实在没有就退回奇偶兜底。
    """
    poses = cfg.get("poses")
    if isinstance(poses, list) and poses:
        return str(poses[idx % len(poses)])
    explicit = str((scene or {}).get("pose") or "").strip()
    if explicit:
        return explicit
    text = " ".join(str((scene or {}).get(k) or "")
                    for k in ("say", "text", "caption"))
    hit = _keyword_pose(text)
    # 一句话前半句说结论、后半句说缺口时，整句关键词会把后半句的态度提前演到
    # 第一秒（「只认十四个」还没说完就叉腰）。两半句语义不同就先演前半句。
    head_want, tail_want = _clause_wants(str((scene or {}).get("say") or ""))
    head_first = False
    if head_want and tail_want and head_want != tail_want:
        hw = float(_keyword_scores(text).get(head_want) or 0.0)
        tw = float(_keyword_scores(text).get(tail_want) or 0.0)
        if hw >= tw * 0.85:
            hit, head_first = head_want, True
    # 半句顺序定下来就不再让「别和前两屏撞」把它翻回去：翻了就等于前半句演
    # 后半句的态度（「只认十四个」还没出口先叉腰）。宁可连演两屏同一个手势，
    # 也不能把一句话里的先后演反。
    if head_first and hit not in ("wave", "happy"):
        return hit
    # 语义命中会因为「跟上一屏撞了」被让位；让不出去时还得回到它，
    # 否则就落进奇偶兜底，选出一个这句话根本没说过的动作（体检会判错配）。
    semantic = hit
    recent = {x for x in (avoid, avoid_extra) if x}
    if recent and hit in recent:
        # 同分线索里挑一个跟前两屏都不同的；都没有就当作没有线索，交给兜底
        scores = {k: v for k, v in _keyword_scores(text).items() if k not in recent}
        hit = max(scores, key=lambda k: round(scores[k], 3)) if scores else ""
    # 开场挥手、收尾高兴是默认礼数；但这句话本身在发问或在说缺口，
    # 还挥手/傻笑就假了——语义优先于礼数。
    if hit and hit not in ("wave", "happy"):
        # 语义命中就别让「开场挥手/收尾傻笑」压掉：收尾那句是「没想到」，
        # 给个惊喜比给个笑更贴。
        if seed:
            scores = _keyword_scores(text)
            top = float(scores.get(hit) or 0.0)
            cands = [hit] + sorted(
                (k for k, v in scores.items()
                 if k != hit and k not in recent and k not in ("wave", "happy")
                 and float(v) > 0 and float(v) >= top * 0.55),
                key=lambda k: -float(scores[k]))
            return _variant_pick(cands, seed)
        return hit
    if idx == 0:
        return "wave"       # 开场挥手，先把人立住
    if idx >= total - 1:
        return "happy"      # 收尾给情绪高点
    if hit:
        return hit
    if semantic:
        return semantic
    return "point" if idx % 2 else "think"


def _character_mouth(idx: int, speaking: bool, scene: dict[str, Any]) -> str:
    """问句收尾把嘴留住圆口，感叹句张大，其余按镜头轮换（全片一个口型像冻住）。"""
    if not speaking:
        return "closed"
    tail = str((scene or {}).get("say") or "").rstrip()
    if tail.endswith(("？", "?")):
        return "round"
    if tail.endswith(("！", "!")):
        return "open"
    return ("open", "half", "open", "round")[idx % 4]


def _stage_canvas(figure: Any, cfg: dict[str, Any], width: int, height: int) -> Any:
    """把一张立绘摆进整幅透明画布：影子、轮廓柔光、按内容框贴底。

    `_character_frame` 一屏画一张；人物动画层逐帧走同一套摆位——
    两处只要有一处改歪，动起来的人和停住的人就会错位。
    """
    from PIL import Image, ImageDraw

    w = int(float(cfg.get("width") or 620) * width / 1080)
    fig = figure.resize((w, w), Image.LANCZOS)
    # 立绘四周有透明留白：按内容框对齐，否则右边缘会把鲸尾切掉
    bb = fig.getbbox() or (0, 0, w, w)
    cw, chh = bb[2] - bb[0], bb[3] - bb[1]
    # 620 宽 + 底边压在 1650：比卡面低一档，又不和底部字幕抢位置
    x = int(float(cfg.get("x") or 560) * width / 1080)
    y = int(float(cfg.get("y") or 1014) * height / 1920)
    x = max(0, min(x, width - 32 - cw))
    y = max(0, min(y, height - chh))
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    # 脚下压一层模糊椭圆当影子：没有它，立绘读起来就是贴在画面上的一张图
    from PIL import ImageFilter

    shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    cx = x + cw * 0.5
    cy = y + chh * 0.95
    sd.ellipse((cx - cw * 0.40, cy - chh * 0.055, cx + cw * 0.40, cy + chh * 0.045),
               fill=(0, 0, 0, 90))
    canvas.alpha_composite(shade.filter(ImageFilter.GaussianBlur(max(6, cw // 22))))
    # 深色卡面上蓝发立绘会和背景糊在一起：沿轮廓扩一圈柔光把她从底上摘出来
    # 光晕不能跟着每一缕头发走：直接把轮廓高斯模糊，发梢会被描成一圈亮边，
    # 看着像贴纸。先缩到 1/7 抹掉细节，再放大成一团模糊的云，读起来才是环境光。
    gw = int(w * 1.16)
    cut = max(24, gw // 7)
    ga = fig.split()[-1].resize((cut, cut), Image.LANCZOS)
    ga = ga.filter(ImageFilter.GaussianBlur(max(3, cut // 9)))
    ga = ga.resize((gw, gw), Image.LANCZOS)
    ga = ga.filter(ImageFilter.GaussianBlur(max(6, gw // 30)))
    # 只模糊轮廓会在方形画布边界上留一圈硬边，深色卡面上看就是一个亮方块。
    # 再乘一层径向遮罩，让光晕在边缘自然消失。
    from PIL import ImageChops

    mask = Image.new("L", (gw, gw), 0)
    ImageDraw.Draw(mask).ellipse((gw * 0.06, gw * 0.06, gw * 0.94, gw * 0.94), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(gw // 9))
    ga = ImageChops.multiply(ga, mask)
    # 冷白偏蓝的光，压到 0.16：深色卡面上刚够把她从背景里托起来，
    # 再亮就是脖子上套了个光圈
    halo = Image.new("RGBA", (gw, gw), (196, 226, 255, 255))
    halo.putalpha(ga.point(lambda v: int(v * 0.16)))
    canvas.alpha_composite(halo, (int(x + cw * 0.5 - gw * 0.5),
                                 int(y + chh * 0.5 - gw * 0.5)))
    canvas.alpha_composite(fig, (x - bb[0], y - bb[1]))
    return canvas


def _character_frame(cfg: dict[str, Any], idx: int, total: int, job_dir: Path,
                     width: int, height: int, speaking: bool,
                     scene: dict[str, Any] = None,
                     pose: str = "",
                     mouth: str = "",
                     eyes: str = "",
                     expression: str = "") -> Path | None:
    """把立绘贴到一张透明画布上，交回剪映当独立图层（可单独做动效）。"""
    from PIL import Image, ImageDraw

    import character

    pose = pose or str(cfg.get("pose") or _scene_pose(cfg, idx, total, scene or {}))
    mouth = mouth or _character_mouth(idx, speaking, scene or {})
    # 高兴时眯眼，是这套立绘里唯一能读出情绪的五官开关；眨眼由调用方按时间点传
    eyes = eyes or ("closed" if pose == "happy" else "open")
    outfit = str(cfg.get("outfit") or "").strip() or None
    # 表情是独立一层：服装只出手体，情绪符号跨服装共用，同一个人换装不用重画表情
    expression = str(expression or cfg.get("expression") or "").strip() or None
    key = (pose, mouth, eyes, outfit or "", expression or "",
           character.outfit_root(outfit).name)
    figure = _CHAR_FRAME_CACHE.get(key)
    if figure is None:
        figure = character.render(pose=pose, mouth=mouth, eyes=eyes, outfit=outfit,
                                  expression=expression)
        _CHAR_FRAME_CACHE[key] = figure
    canvas = _stage_canvas(figure, cfg, width, height)
    # 文件名必须带口型/姿态：同一屏要贴好几段不同口型，同名会把前一张覆盖掉
    out = job_dir / f"char_{idx:02d}_{pose}_{mouth}.png"
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
    # 别截断 job_id：并发提交的 runId 只有尾部几位不同，砍掉就撞名，
    # 三份草稿会互相覆盖，最后谁后写谁的内容留在盘上。
    name = payload.get("draftName") or f"studio_{job_dir.name}"
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
    say(96, "jianying:copy-check")
    # 文案体检：只提示不改稿，让写稿的人自己决定要不要动。
    copy_report = None
    try:
        from copy_check import check_copy

        raw = payload.get("spec") or {}
        narr = raw.get("narration")
        narr = narr if isinstance(narr, dict) else {}
        pts = [str(x) for x in (raw.get("points") or [])]
        # 口播稿可能写在要点第三段（标题::卡面正文::口播稿），体检要按讲述顺序读
        says = [x.split("::")[2] if x.count("::") >= 2 else "" for x in pts]
        spoken = [str(narr.get("intro") or "")] if narr.get("intro") else []
        fallback = [str(x) for x in (narr.get("points") or [])]
        spoken += [s for s in (says or fallback) if s]
        if narr.get("cta"):
            spoken.append(str(narr["cta"]))
        copy_report = check_copy(
            topic=str(raw.get("topic") or ""),
            hook=str(raw.get("hook") or ""),
            points=pts,
            cta=str(raw.get("cta") or ""),
            narration=spoken,
        )
    except Exception as exc:  # noqa: BLE001 - 体检失败不影响出片
        copy_report = {"findings": [], "metrics": {}, "ok": True, "error": str(exc)[:160]}

    say(100, "jianying:done")
    return {
        "draftPath": str(dest),
        "draftName": name,
        "duration": built["duration"],
        "stats": built.get("stats") or {},
        "copyCheck": copy_report,
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
