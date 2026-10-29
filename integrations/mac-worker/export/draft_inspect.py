"""只读体检一个剪映草稿目录：不启动剪映、不改任何文件。

打开工程前想知道「这份草稿对不对」：画布、时长、素材引用是否都还在。剪映 11.6 保存后
会把 draft_info.json 加密（首个非空白字节不是 `{`），这时内部信息读不到，但草稿本身
仍然是能开的——所以这里把「读不到」如实报出来，不当失败。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MEDIA_SECTIONS = ("videos", "audios", "images")
MEDIA_KEYS = ("path", "file_Path", "file_path")
SIDECAR = "studio_export_meta.json"
# 时长对账容差：一帧（30fps≈33ms），再宽就说明真不一样了
DURATION_TOLERANCE = 0.05


def _load_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        return None  # 加密或损坏，读不了就当没有
    try:
        return json.loads(text)
    except ValueError:
        return None


def inspect(draft_dir: str | Path) -> dict:
    """返回体检结论。键固定，方便上层直接塞进 JSON 输出。"""
    d = Path(draft_dir)
    info: dict[str, Any] = {
        "path": str(d),
        "isDir": d.is_dir(),
        "infoReadable": False,
        "encrypted": False,
        "canvas": None,
        "durationSec": None,
        "fps": None,
        "trackCount": None,
        "media": {name: 0 for name in MEDIA_SECTIONS},
        "missingMedia": [],
        "schema": None,
        "stampState": "missing",
        "expected": None,
        "mismatches": [],
    }
    if not d.is_dir():
        return info

    raw = d / "draft_info.json"
    if not raw.is_file():
        return info
    try:
        head = raw.read_text(encoding="utf-8", errors="ignore")[:64].lstrip()
    except OSError:
        return info
    if not head.startswith("{"):
        info["encrypted"] = True
    data = _load_json(raw)
    if isinstance(data, dict):
        info["infoReadable"] = True
        canvas = data.get("canvas_config") or {}
        if isinstance(canvas, dict) and canvas.get("width") and canvas.get("height"):
            info["canvas"] = {"width": canvas.get("width"), "height": canvas.get("height")}
        duration = data.get("duration")
        if isinstance(duration, (int, float)) and duration > 0:
            info["durationSec"] = round(duration / 1_000_000, 3)
        fps = data.get("fps")
        if isinstance(fps, (int, float)) and fps > 0:
            info["fps"] = fps
        tracks = data.get("tracks")
        if isinstance(tracks, list):
            info["trackCount"] = len(tracks)
        materials = data.get("materials") or {}
        if isinstance(materials, dict):
            for name in MEDIA_SECTIONS:
                items = materials.get(name) or []
                if not isinstance(items, list):
                    continue
                info["media"][name] = len(items)
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    ref = next((item[k] for k in MEDIA_KEYS if isinstance(item.get(k), str) and item[k]), "")
                    if not ref or "://" in ref:
                        continue
                    target = Path(ref)
                    if not target.is_absolute():
                        target = d / ref
                    if not target.exists() and ref not in info["missingMedia"]:
                        info["missingMedia"].append(ref)

    stamp_path = d / "draft_version.json"
    if stamp_path.is_file():
        stamp = _load_json(stamp_path)
        schema = stamp.get("schema") if isinstance(stamp, dict) and isinstance(stamp.get("schema"), dict) else None
        if schema and schema.get("version") is not None:
            info["stampState"] = "present"
            info["schema"] = {
                "version": schema.get("version"),
                "source": schema.get("source"),
                "app": stamp.get("app") if isinstance(stamp.get("app"), str) else None,
            }
        else:
            # 文件在但读不出/没有版本块：和「旧草稿压根没戳」分开报
            info["stampState"] = "unreadable"

    info["expected"] = _expected(d / SIDECAR)
    info["mismatches"] = _mismatches(info, info["expected"])
    return info


def _expected(path: Path) -> dict | None:
    """读我们自己写的 sidecar：这份草稿本该是什么样。读不到就返回 None。"""
    meta = _load_json(path)
    if not isinstance(meta, dict):
        return None
    out: dict[str, Any] = {"runId": meta.get("runId"), "revision": meta.get("revision"),
                           "adapterVersion": meta.get("adapterVersion")}
    canvas = meta.get("expectedCanvas") or meta.get("canvas") or {}
    if isinstance(canvas, dict) and canvas.get("width") and canvas.get("height"):
        out["canvas"] = {"width": canvas.get("width"), "height": canvas.get("height")}
    duration = meta.get("expectedDuration")
    if not isinstance(duration, (int, float)) and isinstance(meta.get("duration_us"), (int, float)):
        duration = meta["duration_us"] / 1_000_000
    if isinstance(duration, (int, float)) and duration > 0:
        out["durationSec"] = round(float(duration), 3)
    if isinstance(meta.get("fps"), (int, float)) and meta["fps"] > 0:
        out["fps"] = meta["fps"]
    if isinstance(meta.get("tracks"), int):
        out["trackCount"] = meta["tracks"]
    return out


def _mismatches(info: dict, expected: dict | None) -> list[str]:
    """草稿实况 vs sidecar 期望值。没有 sidecar 就不比，别凭空造失败。"""
    if not expected or not info["infoReadable"]:
        return []
    bad = []
    want_canvas, got_canvas = expected.get("canvas"), info.get("canvas")
    if want_canvas and got_canvas != want_canvas:
        bad.append("canvas %sx%s != %sx%s" % (got_canvas.get("width"), got_canvas.get("height"),
                                              want_canvas["width"], want_canvas["height"]))
    want_dur, got_dur = expected.get("durationSec"), info.get("durationSec")
    if want_dur is not None and (got_dur is None or abs(got_dur - want_dur) > DURATION_TOLERANCE):
        bad.append("duration %s != %ss" % (got_dur, want_dur))
    want_fps, got_fps = expected.get("fps"), info.get("fps")
    if want_fps is not None and got_fps != want_fps:
        bad.append("fps %s != %s" % (got_fps, want_fps))
    want_tracks, got_tracks = expected.get("trackCount"), info.get("trackCount")
    if want_tracks is not None and got_tracks != want_tracks:
        bad.append("tracks %s != %s" % (got_tracks, want_tracks))
    return bad
