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

import jianying_versions as VERSIONING

class JianyingError(RuntimeError):
    pass


HOME = Path.home()
VCUT_DIR = HOME / "CreatorStudio" / "jianying" / "VectCutAPI"
VCUT_PY = VCUT_DIR / ".venv" / "bin" / "python"
VCUT_PORT = int(os.getenv("VCUT_PORT", "9100"))
VCUT_BASE = f"http://127.0.0.1:{VCUT_PORT}"

DRAFT_ROOT = HOME / "Movies" / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"

# 出片侧靠这个版本号判断「草稿是哪个适配器写的」，语义变了必须加一
ADAPTER_VERSION = "jianying-engine/4"


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

def _deploy(draft_dir: Path, project_name: str,
            sidecar_extra: dict[str, Any] | None = None,
            allow_overwrite: bool = False) -> Path:
    """把 VectCutAPI 产出的草稿拷进剪映草稿根目录（macOS 无 auto_deploy）。

    **修订版必须落独立目录。** 旧实现是同名目录先 rename 成 `.bak` 再 copytree，
    等于把用户正在剪映里编辑的那份草稿整目录搬走——草稿列表里的条目会变成
    另一条片的时间线，正在手工调整的字幕/转场全部丢失。现在默认不覆盖：
    目录已存在就追加 `__v1/__v2…` 另起一个，冲突只增不毁；只有显式
    `allow_overwrite=True`（人工修草稿）才走原来的 rename 路径。
    """
    DRAFT_ROOT.mkdir(parents=True, exist_ok=True)
    dest = DRAFT_ROOT / project_name
    if dest.exists() and not allow_overwrite:
        for n in range(1, 100):
            cand = DRAFT_ROOT / f"{project_name}__v{n}"
            if not cand.exists():
                dest = cand
                break
        else:
            raise JianyingError(f"草稿目录重名过多，无法为新修订版分配独立目录: {project_name}")
    elif dest.exists():
        backup = dest.with_suffix(dest.suffix + ".bak")
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        dest.rename(backup)
    shutil.copytree(draft_dir, dest)

    deployed_name = dest.name
    meta_path = dest / "draft_meta_info.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["draft_name"] = deployed_name
        meta["draft_fold_path"] = str(dest)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _normalize_draft(dest, deployed_name)
    media = _relocate_media(dest)
    _write_export_sidecar(dest, deployed_name, extra=sidecar_extra)
    os.utime(dest, None)
    return dest


MEDIA_KEYS = ("path", "file_path", "draft_path")


def _relocate_media(dest: Path) -> dict[str, Any]:
    """把草稿引用的外部素材搬进草稿目录，否则剪映直接判「暂无访问权限」。

    剪映 mac 版是 App Sandbox（entitlements: app-sandbox=true +
    assets.movies.read-write），只能读 `~/Movies` 这类它自己获准的容器外目录。
    我们的素材放在 `~/CreatorStudio/...`，沙盒里根本没有这个路径的读取权，
    系统也不会弹授权窗（这是沙盒，不是 TCC），打开草稿就是 26 条红色
    「暂无访问权限」+「链接媒体」弹窗，0/26。

    修法：部署时把每个外部媒体文件复制进 `<草稿>/assets/`，再把 JSON 里的
    绝对路径改指过去——草稿目录在 `~/Movies` 下，沙盒直接可读。
    """
    content_path = dest / "draft_content.json"
    info_path = dest / "draft_info.json"
    if not content_path.exists():
        return {"skipped": "no draft_content.json"}
    assets = dest / "assets"
    root = str(dest)
    copies: dict[str, str] = {}
    missing: list[str] = []

    def fix(value: str) -> str:
        if not value or not value.startswith("/") or value.startswith(root + "/"):
            return value
        src = Path(value)
        if not src.is_file():
            missing.append(value)
            return value
        if value in copies:
            return copies[value]
        assets.mkdir(parents=True, exist_ok=True)
        target = assets / src.name
        if target.exists() and target.stat().st_size != src.stat().st_size:
            target = assets / ("%s_%s" % (hashlib.sha1(value.encode()).hexdigest()[:8], src.name))
        if not target.exists():
            shutil.copy2(src, target)
        copies[value] = str(target)
        return str(target)

    def walk(node):
        if isinstance(node, dict):
            for k, v in list(node.items()):
                if isinstance(v, str) and k in MEDIA_KEYS:
                    node[k] = fix(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    content = json.loads(content_path.read_text(encoding="utf-8"))
    walk(content)
    payload = json.dumps(content, ensure_ascii=False, indent=2)
    if copies:
        content_path.write_text(payload, encoding="utf-8")
        _write_plain_json(info_path, payload)
        timelines = dest / "Timelines"
        if timelines.is_dir():
            for td in timelines.iterdir():
                if td.is_dir():
                    for name in ("draft_content.json", "draft_info.json"):
                        f = td / name
                        if f.exists():
                            f.write_text(payload, encoding="utf-8")
    return {"copied": len(copies), "missing": missing[:10], "missingCount": len(missing)}


# 导出端读的必须字段：缺一个就无法把产物绑到任务/修订版，宁可写盘时就炸。
SIDECAR_REQUIRED = ("runId", "revision", "draftId", "sourceSpecHash", "appBuild",
                    "adapterVersion", "expectedCanvas", "expectedDuration")


def _sidecar_extra(payload: dict[str, Any], spec: dict[str, Any],
                   built: dict[str, Any]) -> dict[str, Any]:
    """草稿的修订版身份：写盘那一刻定死，导出端只认这份，不靠目录名猜。"""
    run_id = str(payload.get("runId") or "")
    raw_rev = str(payload.get("revision") or "").strip()
    revision = int(raw_rev) if raw_rev.isdigit() else None
    return {
        "runId": run_id or None,
        "revision": revision,
        "draftId": built.get("draft_id"),
        "sourceSpecHash": _spec_hash(spec),
        "appBuild": payload.get("appBuild") or None,
        "adapterVersion": ADAPTER_VERSION,
        "expectedCanvas": {"width": int(spec.get("width") or 1920),
                           "height": int(spec.get("height") or 1080)},
        "expectedDuration": round(float(built.get("duration") or 0.0), 3),
        "capabilities": (built.get("report") or {}).get("counts") or {},
        # 草稿是中间态：导出端据此知道 videoPath 还不存在
        "intermediate": True,
    }


def _write_export_sidecar(dest: Path, project_name: str,
                          extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """把"这次草稿是什么规格"写到草稿目录，给导出环节当唯一可信来源。

    为什么需要：剪映 11.6 **保存后会把 draft_info.json 加密**（实测 draft_info.json
    首字节变成 `GGHbtvxhfjg...` 这种密文），所以导出时读不到画布尺寸/时长。
    我们自己的规格必须在写明文的那一刻就落盘。

    `extra` 带修订版身份（runId / revision / draftId / sourceSpecHash …），
    导出端只认这份 sidecar：草稿目录名可能因为重名被改成 `__v1`，
    而 runId+revision 不会因为目录改名而对不上。
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
    try:
        from export.media_digest import digest as _digest, refs_from_materials
    except ImportError:  # 从仓库根以外的地方跑：按文件路径加载
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "media_digest", Path(__file__).resolve().parent / "export" / "media_digest.py")
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _digest, refs_from_materials = _mod.digest, _mod.refs_from_materials
    mat = data.get("materials") or {}
    side["mediaDigest"] = _digest(refs_from_materials(mat), dest)
    version_file = dest / "draft_version.json"
    if version_file.exists():
        try:
            side["schema"] = json.loads(version_file.read_text(encoding="utf-8")).get("schema")
        except Exception:  # noqa: BLE001 - 版本记录坏了不能挡住导出
            side["schema"] = None
    if extra:
        missing = [k for k in SIDECAR_REQUIRED if extra.get(k) in (None, "", {})]
        # runId/appBuild 允许为空（本地复跑没有这些），但键必须存在：
        # 键都没有说明调用方根本没打算绑定任务，那就别写出一个看着像成功的 sidecar
        absent = [k for k in SIDECAR_REQUIRED if k not in extra]
        if absent:
            raise JianyingError(f"sidecar 缺字段 {absent}：导出端无法把产物绑到任务")
    # 修订版身份最后覆盖：它是导出端绑定任务的钥匙，不能被推导值盖掉
    for key, value in (extra or {}).items():
        if value is not None:
            side[key] = value
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

    # 0.5) schema 版本对齐本机装的剪映：VectCutAPI 模板写的是 10.2/windows，
    # 11.6 能容忍旧版本号，但没有理由继续替新版本赌。这里抄本机草稿的
    # version/new_version/platform 块，只在拿不到参考时退回内置表。
    schema = VERSIONING.resolve_schema()
    if schema.get("apply"):
        schema_info = VERSIONING.stamp(content, schema, os_name="mac")
        schema_info["action"] = "applied"
    else:
        # 没有同主版本参考就不动版本块：模板的 10.2/windows 在 11.6 上实测能开，
        # 「推测新版本号」比「沿用旧版本号」风险大得多。
        schema_info = {"action": "kept", "source": schema.get("source")}

    # 1) content 本体 + draft_info.json（11.6 真正读取的文件）
    content["id"] = new_id
    payload = json.dumps(content, ensure_ascii=False, indent=2)
    content_path.write_text(payload, encoding="utf-8")
    wrote_info = _write_plain_json(dest / "draft_info.json", payload)
    (dest / "draft_version.json").write_text(
        json.dumps({"app": schema.get("app"), "schema": {
            "version": schema.get("version"), "new_version": schema.get("new_version"),
            "source": schema.get("source"),
            "referenceDraft": schema.get("referenceDraft")},
            "stamp": schema_info}, ensure_ascii=False, indent=2), encoding="utf-8")

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


# ------------------------------------------------------- 素材探测 / 能力账本

_PROBE_CACHE: dict[str, dict[str, Any]] = {}


def _spec_hash(spec: dict[str, Any]) -> str:
    """spec 的稳定指纹：导出端用它判断「这条片子的草稿是不是同一份 spec 写的」。

    只取指纹不存原文——剪映存盘后 draft_info.json 被加密，事后无法回读。
    """
    blob = json.dumps(spec, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _media_path(value: Any) -> str:
    """片段来源既可能是路径字符串，也可能是 {path, sourceIn, speed, fit} 字典。"""
    if isinstance(value, dict):
        for key in ("path", "file", "uri", "url", "src"):
            if value.get(key):
                return str(value[key])
        return ""
    return str(value or "")


def _probe_media(path: Path) -> dict[str, Any]:
    """ffprobe 读视频真实尺寸/时长；读不到就抛，绝不拿 0 当已知去铺轨。

    为什么要单独一条：旧主路径只认图片，视频参数（sourceIn/sourceOut/speed/fit）
    全都没落地；而 fit 需要真实像素尺寸，猜错就是整屏变形。
    """
    key = str(path)
    if key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", key],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise JianyingError(f"ffprobe 无法执行: {exc}") from None
    if proc.returncode != 0:
        raise JianyingError(
            f"ffprobe 失败(exit {proc.returncode}): {(proc.stderr or '').strip()[:200]}")
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise JianyingError(f"ffprobe 输出不是 JSON: {exc}") from None
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise JianyingError(f"不是视频（没有 video 流）: {path}")
    try:
        duration = float(fmt.get("duration") or video.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        raise JianyingError(f"读不到视频时长: {path}")
    out = {"path": key, "duration": duration,
           "width": int(video.get("width") or 0),
           "height": int(video.get("height") or 0),
           "has_audio": any(s.get("codec_type") == "audio" for s in streams)}
    _PROBE_CACHE[key] = out
    return out


def _fit_scale(fit: Any, src_w: int, src_h: int, canvas_w: int, canvas_h: int,
               scene_idx: int) -> tuple[float, float]:
    """fit → 片段缩放：cover 填满并裁切 / contain 完整含进 / stretch 拉伸 / original 原尺寸。"""
    name = str(fit or "cover").strip().lower()
    if name in ("original", "none", "1:1"):
        return 1.0, 1.0
    if not (src_w and src_h and canvas_w and canvas_h):
        raise JianyingError(f"scene {scene_idx}: 素材或画布尺寸未知，算不出 fit={fit}")
    sx, sy = canvas_w / src_w, canvas_h / src_h
    if name in ("stretch", "fill", "拉伸"):
        return round(sx, 4), round(sy, 4)
    if name in ("contain", "fit", "完整"):
        k = min(sx, sy)
        return round(k, 4), round(k, 4)
    if name in ("cover", "crop", "裁切"):
        k = max(sx, sy)
        return round(k, 4), round(k, 4)
    raise JianyingError(f"scene {scene_idx}: 不认识的 fit={fit}（cover/contain/stretch/original）")


def _empty_report() -> dict[str, Any]:
    """原生能力账本：每一件"想用但没用上"的东西都必须落进四类之一。

    旧实现把所有失败 append 进 `stats.skipped`，然后照样返回成功——调用方看到
    `stats` 只会以为"转场/特效/字体都上了"，实际上可能一个都没上。
    """
    return {"applied": [], "unsupported": [], "missing": [], "requires_user": []}


def _note(report: dict[str, Any], bucket: str, kind: str, value: Any,
          scene: int | None = None, reason: str = "", **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"kind": kind, "value": value, "reason": reason}
    if scene is not None:
        row["scene"] = scene
    row.update(extra)
    report[bucket].append(row)
    return row


def _finalize_report(report: dict[str, Any]) -> dict[str, Any]:
    counts = {bucket: len(rows) for bucket, rows in report.items()}
    report["counts"] = counts
    # ok=False 表示这条草稿**缺东西**，调用方不能把它当"全部生效"往上报
    report["ok"] = not counts["unsupported"] and not counts["missing"]
    report["complete"] = not (counts["unsupported"] or counts["missing"]
                             or counts["requires_user"])
    return report


def _audio_track_specs(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """把 spec 里各种写法的音轨收成统一结构，一条规格 = 一条独立轨道。"""
    rows: list[dict[str, Any]] = []

    def add(value: Any, kind: str, **defaults: Any) -> None:
        for item in _as_list(value):
            if not item:
                continue
            row = dict(defaults)
            if isinstance(item, dict):
                row.update({k: v for k, v in item.items() if v is not None})
            row["path"] = _media_path(item)
            if not row["path"]:
                continue
            row["kind"] = str(row.get("kind") or kind)
            rows.append(row)

    add(spec.get("audioTracks"), "voiceover")
    # 竖屏链路把配乐写在 music 里（渲染器吃 music），老草稿链路只认 bgm；
    # 两种写法都要落到同一条 BGM 轨，不能因为字段名不同就静默丢音乐。
    add(spec.get("bgm") or spec.get("music"), "bgm", loop=True, volume=0.35)
    add(spec.get("voiceover") if spec.get("voiceover") is not None
        else spec.get("narrationAudio"), "voiceover")
    return rows


def _add_audio_tracks(spec: dict[str, Any], draft_id: str, total: float,
                      stats: dict[str, Any], report: dict[str, Any]) -> float:
    """铺独立音轨（旁白 / BGM）：每条 track_name 不同，谁也不挤占谁的轨。

    旧主路径只有每屏一条配音，BGM 无处可放；且失败一律静默。
    """
    end = total
    for row in _audio_track_specs(spec):
        path = Path(str(row["path"])).expanduser()
        kind = str(row.get("kind") or "voiceover")
        track = str(row.get("track") or row.get("trackName")
                    or ("audio_bgm" if kind == "bgm" else "audio_vo"))
        if not path.exists():
            _note(report, "missing", f"audio:{kind}", str(path), None,
                  "音轨文件不存在，这一轨没有进草稿")
            continue
        length = _dur(path)
        if length <= 0:
            _note(report, "unsupported", f"audio:{kind}", str(path), None,
                  "ffprobe 读不到时长，不按猜测的时长铺轨")
            continue
        try:
            volume = float(row.get("volume", 0.35 if kind == "bgm" else 1.0))
            speed = float(row.get("speed") or 1.0)
            if speed <= 0:
                raise ValueError(f"speed 必须 > 0，拿到 {speed}")
            start = float(row.get("start") or 0.0)
            src_in = float(row.get("sourceIn") or 0.0)
            src_out = min(float(row.get("sourceOut") or length), length)
            seg_src = (src_out - src_in) / speed
            if seg_src <= 0.05:
                raise ValueError(f"可用片段只有 {seg_src:.2f}s")
            loop = bool(row.get("loop", kind == "bgm"))
            covered = 0.0
            pieces = 0
            while True:
                left = max(0.0, total - start - covered)
                seg = min(seg_src, left) if loop else seg_src
                if seg <= 0.05:
                    break
                _post("/add_audio", {
                    "draft_id": draft_id,
                    "audio_url": str(path),
                    "start": round(src_in, 3),
                    "end": round(src_in + seg * speed, 3),
                    "target_start": round(start + covered, 3),
                    "duration": round(seg, 3),
                    "speed": speed,
                    "volume": volume,
                    "track_name": track,
                })
                pieces += 1
                covered += seg
                stats["audios"] += 1
                if not loop:
                    break
            if pieces == 0:
                raise ValueError(f"起播点 {start:.2f}s 已经超出片长 {total:.2f}s")
            stats["audio_tracks"] += 1
            end = max(end, start + covered)
            _note(report, "applied", f"audio:{kind}", str(path), None,
                  f"{pieces} 段 / 共 {covered:.2f}s / 轨 {track}",
                  track=track, segments=pieces, seconds=round(covered, 3),
                  volume=volume, loop=loop)
        except Exception as exc:  # noqa: BLE001 - 单轨失败不能毁掉整条草稿
            _note(report, "unsupported", f"audio:{kind}", str(path), None,
                  str(exc)[:200])
    return end


FIT_NAMES = ("cover", "contain", "stretch", "original")


def _scene_video_clips(scene: dict[str, Any], native: dict[str, Any],
                       spec: dict[str, Any], idx: int, job_dir: Path) -> list[dict[str, Any]]:
    """解析这一屏的视频片段，字段与 export/timeline_render.py 的 `video[]` 对齐：
    `src` / `in` / `out` / `fit` / `speed`（`out` 可省，省了按本屏目标时长推）。

    顶层 `spec["video"]` 只有在条数与 `scenes` 完全一致时才按序号对应——
    条数不符就说明对不上字幕/配音，宁可报错也不猜。
    """
    raw = scene.get("video") or native.get("video")
    if not raw and isinstance(spec.get("video"), list):
        top = spec["video"]
        if len(top) == len(spec.get("scenes") or []):
            raw = top[idx]
    clips: list[dict[str, Any]] = []
    for cidx, item in enumerate(_as_list(raw)):
        if isinstance(item, dict):
            src = str(item.get("src") or _media_path(item))
            src_in = float(item.get("in") or 0.0)
            out = item.get("out")
            out = float(out) if out is not None else None
            fit = str(item.get("fit") or "cover").strip().lower()
            speed = float(item.get("speed") or 1.0)
            track = str(item.get("track") or "video_main")
        else:
            src, src_in, out, fit, speed, track = _media_path(item), 0.0, None, "cover", 1.0, "video_main"
        if not src:
            raise JianyingError(f"scene {idx} video[{cidx}]: 缺 src")
        if fit not in FIT_NAMES:
            raise JianyingError(f"scene {idx} video[{cidx}]: fit 只能是 {FIT_NAMES}，得到 {fit!r}")
        if speed <= 0:
            raise JianyingError(f"scene {idx} video[{cidx}]: speed 必须 > 0，得到 {speed}")
        if out is not None and out <= src_in:
            raise JianyingError(
                f"scene {idx} video[{cidx}]: out({out}) 必须大于 in({src_in})")
        path = Path(src).expanduser()
        if not path.is_absolute():
            path = (job_dir / path)
        if not path.is_file():
            raise JianyingError(f"scene {idx} video[{cidx}] 素材不存在: {path}")
        clips.append({"src": path, "in": src_in, "out": out, "fit": fit,
                      "speed": speed, "track": track})
    return clips


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
        # 显式时长 > 配音实测 > 字数估算；素材随 spec 走，不让 TTS 反推镜头长度
        if native.get("duration") or scene.get("duration"):
            dur = float(native.get("duration") or scene.get("duration"))
        clips = _scene_video_clips(scene, native, spec, idx, job_dir)
        plan.append({"scene": scene, "native": native, "duration": dur,
                     "audio": audio, "clips": clips})

    total = 0.0
    stats = {"images": 0, "texts": 0, "effects": 0, "audios": 0,
             "transitions": 0, "text_anims": 0, "fonts": 0, "keyframes": 0,
             "characters": 0, "videos": 0, "audio_tracks": 0, "subtitles": 0}
    # 原生资源账本：apply/unsupported/missing/requires_user 四类，不再有 stats.skipped
    report = _empty_report()
    for idx, item in enumerate(plan):
        scene, native = item["scene"], item["native"]
        dur = item["duration"]
        say(35 + int(55 * idx / max(1, len(plan))),
            f"jianying:scene{idx + 1}/{len(plan)}")

        # ---- 1) 画面轨：视频优先（有视频就不用静帧），其次图片
        # 转场只能挂在片段上，挂不到文字上。
        placed = 0.0
        clips = item.get("clips") or []
        for cidx, clip_src in enumerate(clips):
            if dur - placed <= 0.04:
                break
            try:
                probe = _probe_media(clip_src["src"])
            except JianyingError as exc:
                _note(report, "unsupported", "video", str(clip_src["src"]), idx,
                      f"{exc}（这一片段没进草稿）")
                continue
            speed = float(clip_src["speed"] or 1.0)
            src_in = float(clip_src["in"])
            limit = min(clip_src["out"] if clip_src["out"] is not None
                        else probe["duration"], probe["duration"])
            if src_in >= limit - 0.04:
                _note(report, "unsupported", "video", str(clip_src["src"]), idx,
                      f"in={src_in:.3f}s 超出素材可用范围 {limit:.3f}s")
                continue
            seg = min(dur - placed, (limit - src_in) / speed)
            scale_x, scale_y = _fit_scale(clip_src["fit"], probe["width"],
                                          probe["height"], width, height, idx)
            body: dict[str, Any] = {
                "draft_id": draft_id,
                "video_url": str(clip_src["src"]),
                "start": round(src_in, 3),
                # add_video 的语义：end 是**源**时间（秒），不是目标时长
                "end": round(src_in + seg * speed, 3),
                "target_start": round(total + placed, 3),
                "duration": round(seg, 3),
                "speed": speed,
                "width": width,
                "height": height,
                "scale_x": scale_x,
                "scale_y": scale_y,
                "track_name": clip_src["track"],
                "volume": float(scene.get("volume", 1.0)),
            }
            transition = _pick(native.get("transition_in"), "transitions")
            if transition and cidx == 0 and idx < len(plan) - 1:
                td = float(native.get("transition_duration") or 0.6)
                if seg < 2.6:
                    td = min(td, 0.45)
                body["transition"] = transition
                body["transition_duration"] = td
                stats["transitions"] += 1
            _post("/add_video", body)
            stats["videos"] += 1
            placed += seg
            _note(report, "applied", "video", str(clip_src["src"]), idx,
                  f"{probe['width']}x{probe['height']} 源 {src_in:.2f}→{src_in + seg * speed:.2f}s"
                  f" 目标 {seg:.2f}s speed={speed:g} fit={clip_src['fit']}",
                  sourceIn=round(src_in, 3),
                  sourceOut=round(src_in + seg * speed, 3),
                  targetStart=round(total, 3), targetDuration=round(seg, 3),
                  speed=speed, fit=clip_src["fit"], track=clip_src["track"],
                  scale=[scale_x, scale_y])

        # 素材比这一屏短：把最后一段再铺一次填满，避免后面的字幕压在空白上
        guard = 0
        while clips and placed > 0.04 and dur - placed > 0.05 and guard < 4:
            guard += 1
            last = clips[-1]
            probe = _PROBE_CACHE.get(str(last["src"])) or {}
            if not probe:
                break
            speed = float(last["speed"] or 1.0)
            src_in = float(last["in"])
            limit = min(last["out"] if last["out"] is not None else probe["duration"],
                        probe["duration"])
            seg = min(dur - placed, max(0.0, (limit - src_in) / speed))
            if seg <= 0.04:
                _note(report, "requires_user", "video:fill", str(last["src"]), idx,
                      f"片尾还差 {dur - placed:.2f}s，素材已无可复用片段，请人工补镜")
                break
            sx, sy = _fit_scale(last["fit"], probe["width"], probe["height"],
                                width, height, idx)
            _post("/add_video", {
                "draft_id": draft_id,
                "video_url": str(last["src"]),
                "start": round(src_in, 3),
                "end": round(src_in + seg * speed, 3),
                "target_start": round(total + placed, 3),
                "duration": round(seg, 3),
                "speed": speed,
                "width": width, "height": height,
                "scale_x": sx, "scale_y": sy,
                "track_name": last["track"],
            })
            stats["videos"] += 1
            placed += seg
            _note(report, "applied", "video:fill", str(last["src"]), idx,
                  f"复用最后一段补 {seg:.2f}s", segments=1, seconds=round(seg, 3))
        if clips:
            dur = max(dur, placed)

        image = scene.get("image") or native.get("image")
        if image and placed <= 0.04:
            img = Path(_media_path(image)).expanduser()
            if not img.is_absolute():
                img = (job_dir / img)
            if not img.exists():
                _note(report, "missing", "image", str(img), idx,
                      "图片文件不存在，这一屏没有画面")
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
                _note(report, "applied", "image", str(img), idx,
                      f"静帧 {dur:.2f}s", seconds=round(dur, 3))

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
                        _note(report, "unsupported", "keyframe", "uniform_scale", idx,
                              f"推拉关键帧没写进草稿: {exc}")

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
                beats = _lip_beats(char_cfg, item.get("audio"), total, dur)
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
                    _note(report, "unsupported", "character", "鲸鱼娘", idx,
                          f"角色帧生成失败: {exc}")
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
                    _note(report, "unsupported", "character_clip", "鲸鱼娘", idx,
                              f"角色片段没进草稿: {exc}")
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
                        _note(report, "unsupported", "character_keyframe", prop, idx,
                                f"{prop} 关键帧没写进草稿: {exc}")

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
            stats["subtitles"] += len(chunks)
            _note(report, "applied", "subtitle", base["track_name"], idx,
                  f"逐句字幕 {len(chunks)} 条", segments=len(chunks))
        for body in blocks:
            span = body.pop("_span", None) or (total, total + dur)
            payload = {"draft_id": draft_id, "start": span[0], "end": span[1], **body}
            _post("/add_text", payload)
            stats["texts"] += 1

        # ---- 3) 画面特效轨（转场之外的氛围层）
        for eff in _as_list(native.get("effect")):
            effect_type = _pick(eff, "video_scene_effects")
            if not effect_type:
                _note(report, "unsupported", "effect", eff, idx,
                      "本机特效目录里没有这个名字，没进草稿")
                continue
            try:
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
                _note(report, "applied", "effect", effect_type, idx,
                      f"氛围特效 {dur:.2f}s", seconds=round(dur, 3))
            except Exception as exc:  # noqa: BLE001 - 单个特效失败不该毁掉整条片
                _note(report, "unsupported", "effect", eff, idx,
                      f"调用 /add_effect 失败: {exc}"[:200])

        # ---- 3.5) 滤镜：pyJianYingDraft 有 add_filter，但 VectCutAPI 没有 /add_filter
        for want in _as_list(native.get("filter")):
            name = _pick(want, "filters")
            _note(report, "unsupported", "filter", name or want, idx,
                  "VectCutAPI 未暴露 /add_filter，HTTP 这边落不进草稿；"
                  "pyJianYingDraft 的 script.add_filter 可用，缺的是服务端路由",
                  endpoint="/add_filter", fallback="manual")

        # ---- 4) 配音轨（剪映音色）—— 每屏一条，与 BGM 分轨
        audio = item["audio"]
        if audio:
            try:
                _post("/add_audio", {
                    "draft_id": draft_id,
                    "audio_url": str(audio),
                    "target_start": total,
                    "duration": round(dur, 3),
                    "volume": float(native.get("volume") or 1.0),
                    "track_name": "audio_main",
                })
                stats["audios"] += 1
                _note(report, "applied", "audio:narration", str(audio), idx,
                      f"配音 {dur:.2f}s", track="audio_main", seconds=round(dur, 3))
            except Exception as exc:  # noqa: BLE001
                _note(report, "unsupported", "audio:narration", str(audio), idx,
                      f"配音没进草稿: {exc}"[:200])

        total += dur

    # ---- 5) 独立音轨：BGM / 外围旁白（每轨一个 track_name，互不挤占）
    total = _add_audio_tracks(spec, draft_id, total, stats, report)
    _finalize_report(report)
    return {"draft_id": draft_id, "duration": total, "stats": stats,
            "report": report, "ok": report["ok"]}


_CHARACTERS_ROOT = Path("~/CreatorStudio/state/characters").expanduser()
_CHAR_FRAME_CACHE: dict[tuple[str, str], Any] = {}


def _lip_beats(char_cfg: dict[str, Any], audio: Any, start: float,
               dur: float) -> list[tuple[str, float, float]]:
    """这一屏的口型段：跟着配音能量走，不再按屏均分。

    均分节拍跟口播对不上，观众读到的是「嘴在动」而不是「在说这句」。
    有配音就让 vertical_short 的音量分段决定开合，失败再退回按周期兜底——
    口型出问题不该把整条草稿打掉。
    """
    mouths: list[tuple[str, float]] = []
    if audio and char_cfg.get("lip_from_audio", True):
        try:
            try:
                from export.vertical_short import _audio_beats
            except ImportError:      # worker 里 export 不是包时退回按文件目录找
                sys.path.insert(0, str(Path(__file__).resolve().parent / "export"))
                from vertical_short import _audio_beats  # type: ignore[no-redef]
            mouths = _audio_beats(Path(str(audio)), dur, {
                "lip_min": float(char_cfg.get("lip_min") or 0.22),
                "lip_release": float(char_cfg.get("lip_release") or 0.12),
            })
        except Exception:  # noqa: BLE001 - 解码失败就退回固定周期
            mouths = []
    beats: list[tuple[str, float, float]] = []
    if len(mouths) >= 2:
        at = start
        for mouth, sec in mouths:
            beats.append((mouth, at, min(start + dur, at + max(0.05, float(sec)))))
            at += max(0.05, float(sec))
    if not beats:
        period = float(char_cfg.get("lip_period") or 0.30)
        count = max(2, min(8, int(round(dur / period)) or 2))
        seg = dur / count
        for k in range(count):
            end = start + dur if k == count - 1 else start + seg * (k + 1)
            beats.append((("open", "half", "round", "half")[k % 4],
                          start + seg * k, end))
    # 短于 0.24s 的口型人眼看不出是在说话，只是在闪：并进前一段
    merged: list[tuple[str, float, float]] = []
    for mouth, b0, b1 in beats:
        if merged and merged[-1][0] == mouth:
            prev, p0, _ = merged[-1]
            merged[-1] = (prev, p0, b1)
        elif merged and b1 - b0 < 0.24:
            prev, p0, _ = merged[-1]
            merged[-1] = (prev, p0, b1)
        else:
            merged.append((mouth, b0, b1))
    # 尾段一定盖到本屏结束，否则角色轨在片尾留一道空
    if merged and merged[-1][2] < start + dur - 1e-6:
        mouth, b0, _ = merged[-1]
        merged[-1] = (mouth, b0, start + dur)
    return merged


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
    # 第三次换势演的是最后一句：从结尾那句话自己的线索里挑，动作才落在
    # 正在说的那半句上；整句剩下的分数是前面用过的重心，换上去等于往回演。
    tail = _tail_clause(say)
    if tail:
        for name, score in sorted(_keyword_scores(tail).items(), key=lambda kv: -kv[1]):
            if name in (first, second) or float(score) <= 0:
                continue
            return str(name)
        # 结尾那句没有动作线索，就不要拿前面半句的线索顶上：那是往回演。
        return None
    for name, score in sorted(_keyword_scores(say).items(), key=lambda kv: -kv[1]):
        if name in (first, second) or float(score) <= 0:
            continue
        return str(name)
    return None


def _clause_split(say: str) -> list[str]:
    """按句读把一句话劈成段，丢掉过短的碎段。"""
    text = str(say or "").strip()
    if not text:
        return []
    parts, buf = [], ""
    for ch in text:
        if ch in "，,；;。.！!？?：:、":
            if buf.strip():
                parts.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf.strip())
    return [x for x in parts if len(x) >= 3]


def _clause_poses(say: str) -> list[str]:
    """一句话里有几段，就有几个按先后排列的姿态（相邻重复合并，最多三个）。

    一句话常常是「先说现象、再说代价、最后反问」：这三段各演各的动作才像人在讲，
    把整句最高分的姿态全塞给一屏，观众看到的是同一个姿势从头摆到尾。
    """
    out: list[str] = []
    for seg in _clause_split(say):
        hit = _keyword_pose(seg)
        if hit and (not out or out[-1] != hit):
            out.append(hit)
    return out[:3]


def _tail_clause(say: str) -> str:
    """最后一句话（不含句末标点的尾巴）。"""
    text = str(say or "").strip().rstrip("。！？!?…")
    cut = max(text.rfind(ch) for ch in "，,；;。.！!？?：:、") if text else -1
    tail = text[cut + 1:].strip() if cut >= 0 else text
    return tail if len(tail) >= 4 else ""


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
    # 一点线索都没有时，别「为了换而换」：奇偶轮换会让每一屏都换一次手势，
    # 观众看到的是人每隔五秒抽一下。保持上一屏的姿势，等真有话说再换。
    if avoid:
        return avoid
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
    """把一张立绘摆进整幅透明画布：只留脚下的接触阴影，按内容框贴底。

    这里不放任何发光层：轮廓光、亮边、辉光、bloom 在深色卡面上都会读成
    「人物周围有一圈光」，所以只剩阴影（暗）这一种把人物从背景里摘出来的手段。

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
    # 脚下压一层模糊椭圆当接触阴影：没有它，立绘读起来就是贴在画面上的一张图。
    # 这是暗部、不是光：半径和浓度都收着用，只压脚边一小圈。
    from PIL import ImageFilter

    shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    cx = x + cw * 0.5
    cy = y + chh * 0.95
    sd.ellipse((cx - cw * 0.38, cy - chh * 0.045, cx + cw * 0.38, cy + chh * 0.035),
               fill=(0, 0, 0, 96))
    canvas.alpha_composite(shade.filter(ImageFilter.GaussianBlur(max(5, cw // 26))))
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
    run_id = str(payload.get("runId") or "")
    raw_rev = str(payload.get("revision") or "").strip()
    revision = int(raw_rev) if raw_rev.isdigit() else None
    base_name = str(payload.get("draftName") or f"studio_{job_dir.name}")
    # 修订版必须落在自己的名字下：重交一版不能把上一版草稿替换掉
    name = f"{base_name}__r{revision}" if revision else base_name
    saved = _post("/save_draft", {
        "draft_id": built["draft_id"],
        "project_name": name,
        "auto_deploy": False,
    }, timeout=900)

    src = Path(saved.get("draft_url") or "")
    if not src.exists():
        raise JianyingError(f"save_draft produced no folder: {saved}")

    say(90, "jianying:deploy")
    # 草稿存盘后 draft_info.json 被剪映加密，spec 真相只能写盘时落进 sidecar；
    # 修订版身份一并带上，导出端据此绑定任务，不靠文件名猜。
    sidecar_extra = _sidecar_extra(payload, spec, built)
    dest = _deploy(src, name, sidecar_extra=sidecar_extra)
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
    report = built.get("report") or {}
    return {
        "draftPath": str(dest),
        "draftName": name,
        "draftId": built["draft_id"],
        "runId": run_id or None,
        "revision": revision,
        "sourceSpecHash": sidecar_extra["sourceSpecHash"],
        "adapterVersion": ADAPTER_VERSION,
        "expectedDuration": sidecar_extra["expectedDuration"],
        "duration": built["duration"],
        "stats": built.get("stats") or {},
        "report": report,
        "capabilities": report.get("counts") or {},
        "copyCheck": copy_report,
        # 草稿就绪只是中间态：成片要等导出，不要把 draftPath 当成 videoPath 回传
        "stage": "draft_ready",
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
