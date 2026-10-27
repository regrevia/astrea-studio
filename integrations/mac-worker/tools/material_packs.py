#!/usr/bin/env python3
"""素材包引用解析：把 ``pack:<slug>/<name>`` 变成绝对路径，其余写法原样透传。

为什么只做解析层
    出片链路（paper_short / paper_render_engine）只认文件系统里的绝对路径，
    版位语法（``#高#contain#宽``）也已经在 deploy/schematic_preview.py 里处理完。
    素材包只是一个「命名空间」：图放 ``<materials>/packs/<slug>/shots/<name>.png``，
    spec 里写短名字就能引用。所以这里只负责把短名字还原成路径，不碰渲染、不改版位语法。

引用语法
    pack:<slug>/<name>         → <materials>/packs/<slug>/shots/<name>.png
    pack:<slug>/<name>.png     → 同上（带不带后缀都行）
    pack:<slug>/<name>#396#contain#396
                                → 解析路径后把 ``#...`` 原样拼回去，交给渲染层

    MATERIALS_ROOT 环境变量可覆盖根目录（默认 ~/CreatorStudio/materials）；
    packs 根也可以用 ``packs_root`` 参数指定，测试和别的目录布局都用得上。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

PACK_PREFIX = "pack:"
SUFFIX_SEP = "#"


class PackError(ValueError):
    """引用写错了：包不存在、名字不存在、包是空的。消息里带「有哪些可用的」。"""


def materials_root(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("MATERIALS_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / "CreatorStudio" / "materials").resolve()


def packs_root(explicit: str | os.PathLike[str] | None = None) -> Path:
    return materials_root(explicit) / "packs"


def list_packs(root: str | os.PathLike[str] | None = None) -> list[str]:
    p = packs_root(root)
    if not p.is_dir():
        return []
    return sorted(x.name for x in p.iterdir() if x.is_dir())


def list_shots(slug: str, root: str | os.PathLike[str] | None = None) -> list[str]:
    d = packs_root(root) / slug / "shots"
    if not d.is_dir():
        return []
    return sorted(x.stem for x in d.glob("*.png"))


def load_index(slug: str, root: str | os.PathLike[str] | None = None) -> dict[str, Any] | None:
    f = packs_root(root) / slug / "index.json"
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _available_hint(slug: str, root: str | os.PathLike[str] | None) -> str:
    names = list_shots(slug, root)
    if names:
        shown = ", ".join(names[:8]) + ("…" if len(names) > 8 else "")
        return f"包里可用：{shown}"
    packs = list_packs(root)
    shown = ", ".join(packs[:8]) + ("…" if len(packs) > 8 else "") if packs else "（一个都没有）"
    return f"这个包没有图。现有包：{shown}"


def resolve_pack_ref(ref: str, root: str | os.PathLike[str] | None = None) -> Path:
    """``pack:slug/name[.png]`` → 绝对 Path。带 ``#...`` 后缀会先剥掉（由调用方决定怎么拼回）。"""
    if not ref.startswith(PACK_PREFIX):
        raise PackError(f"不是素材包引用：{ref!r}")
    body = ref[len(PACK_PREFIX):]
    body = body.split(SUFFIX_SEP, 1)[0].strip()
    if not body:
        raise PackError("pack: 后面是空的，要写 pack:<slug>/<name>")
    if "/" in body:
        slug, name = body.split("/", 1)
    else:
        slug, name = body, ""
    slug = slug.strip("/")
    name = name.strip("/")
    if not slug:
        raise PackError(f"包名是空的：{ref!r}")

    base = packs_root(root) / slug
    if not base.is_dir():
        packs = list_packs(root)
        shown = ", ".join(packs[:8]) + ("…" if len(packs) > 8 else "") if packs else "（一个都没有）"
        raise PackError(f"素材包不存在：{slug}（现有包：{shown}）")

    if not name:
        # 只写包名时取 index.json 里第一张可用的，让「一包一图」的场景少写一截
        idx = load_index(slug, root) or {}
        shots = idx.get("shots") or []
        if not shots:
            raise PackError(f"素材包 {slug} 的 index.json 没有可用图。{_available_hint(slug, root)}")
        name = str(shots[0].get("name") or "")
        if not name:
            raise PackError(f"素材包 {slug} 的 index.json 第一条没有 name 字段")

    name = Path(name).name  # 不许 ../ 逃出包目录
    for cand in (base / "shots" / name, base / "shots" / f"{name}.png"):
        if cand.is_file():
            return cand.resolve()
    raise PackError(f"素材包 {slug} 里没有 {name}。{_available_hint(slug, root)}")


def resolve_ref(ref: str, root: str | os.PathLike[str] | None = None) -> str:
    """spec 里的一项 → 渲染层能直接吃的字符串。非 pack: 引用保持原语义（波浪号展开）。"""
    if not ref.startswith(PACK_PREFIX):
        return str(Path(ref.split(SUFFIX_SEP, 1)[0]).expanduser()) + "".join(
            SUFFIX_SEP + part for part in ref.split(SUFFIX_SEP)[1:]
        )
    suffix = "".join(SUFFIX_SEP + part for part in ref.split(SUFFIX_SEP)[1:])
    return str(resolve_pack_ref(ref, root)) + suffix


__all__ = [
    "PackError", "PACK_PREFIX", "resolve_ref", "resolve_pack_ref",
    "materials_root", "packs_root", "list_packs", "list_shots", "load_index",
]
