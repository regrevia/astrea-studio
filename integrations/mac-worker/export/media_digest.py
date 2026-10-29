"""草稿引用了哪些素材——算成一个短指纹，给「体检 vs sidecar」对账用。

只认路径本身，不读文件内容：剪映的素材可能被替换/重新压缩，指纹只回答
「这份草稿引用的还是同一批文件吗」。引擎写 sidecar 时算一次，体检草稿时再算一次，
两边必须用同一套归一化（相对草稿目录、posix 分隔、排序、sha1 前 12 位）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

MEDIA_SECTIONS = ("videos", "audios", "images")
MEDIA_KEYS = ("path", "file_Path", "file_path")


def normalize(ref: str, base: Path | None) -> str | None:
    """把素材路径归一成「相对草稿目录的 posix 路径」；URL 和空值不算。"""
    ref = (ref or "").strip()
    if not ref or "://" in ref:
        return None
    path = Path(ref)
    if path.is_absolute():
        if base is not None:
            try:
                return path.relative_to(base).as_posix()
            except ValueError:
                return path.as_posix()
        return path.as_posix()
    return Path(ref.replace("\\", "/")).as_posix()


def _entry(ref: str, base: Path | None) -> str | None:
    """一条素材的指纹原文：相对路径 + 字节数。文件不在了记 `?`，删了也算变了。"""
    name = normalize(ref, base)
    if not name:
        return None
    target = Path(name) if base is None else (base / name)
    try:
        size = target.stat().st_size
    except OSError:
        return "%s|?" % name
    return "%s|%d" % (name, size)


def digest(refs: Iterable[str], base: Path | None = None) -> str:
    """排序后拼接再取 sha1 前 12 位；没有素材时返回空串，不当成「不一致」。

    带字节数是为了认出「路径没变、内容换了」：只认路径的话，同一路径换了素材也算一致。
    不带 mtime：剪映/备份工具会重写 mtime，那是噪声，会造出假阻塞。
    """
    entries = sorted({e for e in (_entry(r, base) for r in refs) if e})
    if not entries:
        return ""
    return hashlib.sha1("\n".join(entries).encode("utf-8")).hexdigest()[:12]


def refs_from_materials(materials: Any) -> list[str]:
    """从 draft_info.materials 里抠出所有素材路径。"""
    out: list[str] = []
    if not isinstance(materials, dict):
        return out
    for name in MEDIA_SECTIONS:
        items = materials.get(name) or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            ref = next((item[k] for k in MEDIA_KEYS if isinstance(item.get(k), str) and item[k]), "")
            if ref:
                out.append(ref)
    return out
