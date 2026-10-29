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

    stamp = _load_json(d / "draft_version.json")
    if isinstance(stamp, dict):
        schema = stamp.get("schema") if isinstance(stamp.get("schema"), dict) else None
        info["schema"] = {
            "version": (schema or {}).get("version"),
            "source": (schema or {}).get("source"),
            "app": stamp.get("app") if isinstance(stamp.get("app"), str) else None,
        }
    return info
