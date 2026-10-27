#!/usr/bin/env python3
"""material_packs 解析层自测：python3 integrations/mac-worker/tools/test_material_packs.py

只用临时目录造包，不依赖真实素材、不联网；跑完打印 ok/fail 并以退出码收尾
（本机 python3.9 的 pytest 因 typing_extensions 报错不可用，所以自带 __main__ 跑法）。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import material_packs as mp  # noqa: E402

CASES: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        fn()
    except AssertionError as e:
        CASES.append((name, False, str(e) or "断言失败"))
    except Exception as e:  # noqa: BLE001 - 自测里任何异常都算这条失败
        CASES.append((name, False, f"{type(e).__name__}: {e}"))
    else:
        CASES.append((name, True, ""))


def make_pack(root: Path, slug: str, names: list[str], index: dict | None = None) -> None:
    shots = root / "packs" / slug / "shots"
    shots.mkdir(parents=True)
    for n in names:
        (shots / f"{n}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    if index is not None:
        (root / "packs" / slug / "index.json").write_text(json.dumps(index), encoding="utf-8")


def with_root(fn):
    def wrapper() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fn(Path(tmp))
    return wrapper


@with_root
def t_basic(root: Path) -> None:
    make_pack(root, "demo", ["hero", "second"])
    got = mp.resolve_ref("pack:demo/hero", root)
    assert got == str((root / "packs/demo/shots/hero.png").resolve()), got


@with_root
def t_suffix_kept(root: Path) -> None:
    make_pack(root, "demo", ["hero"])
    got = mp.resolve_ref("pack:demo/hero#838#contain#396", root)
    assert got.endswith("/hero.png#838#contain#396"), got


@with_root
def t_explicit_png(root: Path) -> None:
    make_pack(root, "demo", ["hero"])
    assert mp.resolve_ref("pack:demo/hero.png", root).endswith("/hero.png")


@with_root
def t_pack_only_uses_first_ok(root: Path) -> None:
    make_pack(root, "demo", ["hero", "zzz"],
              index={"shots": [{"name": "hero", "ok": True}]})
    got = mp.resolve_pack_ref("pack:demo", root)
    assert got.name == "hero.png", got


@with_root
def t_pack_only_without_index_fails(root: Path) -> None:
    make_pack(root, "demo", ["hero"])
    try:
        mp.resolve_pack_ref("pack:demo", root)
    except mp.PackError as e:
        assert "index.json" in str(e), e
    else:
        raise AssertionError("没有 index.json 时应该报错")


@with_root
def t_missing_pack_message(root: Path) -> None:
    make_pack(root, "demo", ["hero"])
    try:
        mp.resolve_pack_ref("pack:nope/hero", root)
    except mp.PackError as e:
        assert "素材包不存在" in str(e) and "demo" in str(e), e
    else:
        raise AssertionError("不存在的包应该报错")


@with_root
def t_missing_shot_lists_available(root: Path) -> None:
    make_pack(root, "demo", ["hero", "second"])
    try:
        mp.resolve_pack_ref("pack:demo/third", root)
    except mp.PackError as e:
        assert "hero" in str(e) and "second" in str(e), e
    else:
        raise AssertionError("不存在的图应该报错并列出可用的")


@with_root
def t_no_escape(root: Path) -> None:
    make_pack(root, "demo", ["hero"])
    try:
        mp.resolve_pack_ref("pack:demo/../../etc/passwd", root)
    except mp.PackError as e:
        assert "没有" in str(e), e
    else:
        raise AssertionError("路径穿越应该被挡")


@with_root
def t_plain_path_passthrough(root: Path) -> None:
    got = mp.resolve_ref("/tmp/abs.png#470#contain#340", root)
    assert got == "/tmp/abs.png#470#contain#340", got
    got2 = mp.resolve_ref("~/x.png", root)
    assert got2 == str(Path("~/x.png").expanduser()), got2
    got3 = mp.resolve_ref("sketch:timeline", root)
    assert got3 == "sketch:timeline", got3


@with_root
def t_env_root( root: Path) -> None:
    make_pack(root, "demo", ["hero"])
    old = os.environ.get("MATERIALS_ROOT")
    os.environ["MATERIALS_ROOT"] = str(root)
    try:
        assert mp.resolve_ref("pack:demo/hero").endswith("/hero.png")
    finally:
        if old is None:
            os.environ.pop("MATERIALS_ROOT", None)
        else:
            os.environ["MATERIALS_ROOT"] = old


@with_root
def t_listing(root: Path) -> None:
    make_pack(root, "b", ["x"])
    make_pack(root, "a", ["y"])
    assert mp.list_packs(root) == ["a", "b"], mp.list_packs(root)
    assert mp.list_shots("a", root) == ["y"]


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            check(name[2:], fn)
    bad = [c for c in CASES if not c[1]]
    for name, ok, err in CASES:
        print(("ok   " if ok else "FAIL ") + name + (f" → {err}" if err else ""))
    print(f"\n{len(CASES) - len(bad)}/{len(CASES)} passed")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
