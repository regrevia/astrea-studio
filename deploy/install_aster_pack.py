#!/usr/bin/env python3
"""把阿斯特总包装进角色库：服装、状态图、动画序列。

总包结构（Aster_Studio_v3_All_In_One）::

    state_map.json                      # 服装 id → 中文名
    assets/outfits/<id>/
      stills_2048/{poses,base,states,shots}/*.png
      runtime_512/{poses,base,states,shots}/*.png
      animations/<idle_blink|talk|wave>/sequence/*.png   # 15fps

落盘后每套服装是： ``outfits/<id>/{poses,base,states,shots,anim/<name>}``，
``meta.json`` 记 label / poses / mouths / animations。已有的图默认不覆盖，
只有 ``--force`` 才换。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

STATIC_DIRS = ("poses", "base", "states", "shots")
ANIM_FPS = 15


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 描述坏了按空处理
        return {}


def _scan_poses(poses_dir: Path) -> tuple[list[str], list[str]]:
    poses: set[str] = set()
    mouths: set[str] = set()
    for p in sorted(poses_dir.glob("pose_*.png")):
        stem = p.stem[len("pose_"):]
        if "_" in stem:
            pose, mouth = stem.rsplit("_", 1)
        else:
            pose, mouth = stem, "open"
        poses.add(pose)
        mouths.add(mouth)
    return sorted(poses), sorted(mouths)


def _copy_static(src: Path, dst: Path, force: bool, dry: bool) -> int:
    copied = 0
    for sub in STATIC_DIRS:
        sdir = src / sub
        if not sdir.is_dir():
            continue
        if not dry:
            (dst / sub).mkdir(parents=True, exist_ok=True)
        for p in sorted(sdir.glob("*.png")):
            target = dst / sub / p.name
            if target.exists() and not force:
                continue
            if not dry:
                shutil.copyfile(p, target)
            copied += 1
    return copied


def _copy_anims(src: Path, dst: Path, force: bool, dry: bool) -> dict:
    anims: dict[str, dict] = {}
    if not src.is_dir():
        return anims
    for adir in sorted(src.iterdir()):
        seq = adir / "sequence"
        if not seq.is_dir():
            continue
        frames = sorted(seq.glob("*.png"))
        if not frames:
            continue
        seqdst = dst / "anim" / adir.name
        if not dry:
            seqdst.mkdir(parents=True, exist_ok=True)
        for p in frames:
            target = seqdst / p.name
            if target.exists() and not force:
                continue
            if not dry:
                shutil.copyfile(p, target)
        meta = {"name": adir.name, "fps": ANIM_FPS,
                "frames": len(frames), "loop": True}
        if not dry:
            (seqdst / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
        anims[adir.name] = {"fps": ANIM_FPS, "frames": len(frames)}
    return anims


def _write_meta(dst: Path, oid: str, label: str, canvas: int,
                anims: dict, dry: bool) -> dict:
    meta = _read_json(dst / "meta.json") or {}
    poses, mouths = _scan_poses(dst / "poses")
    meta["id"] = oid
    meta["label"] = meta.get("label") or label or oid
    meta.setdefault("mode", "baked")
    meta["poses"] = poses or meta.get("poses") or []
    meta["mouths"] = mouths or meta.get("mouths") or []
    meta["canvas"] = int(meta.get("canvas") or canvas)
    if anims:
        meta["animations"] = anims
    if not dry:
        (dst / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta


def install(pack: Path, root: Path, only: list[str], force: bool, dry: bool) -> list[dict]:
    state_map = _read_json(pack / "state_map.json")
    labels = {str(o.get("id")): str(o.get("label") or "")
              for o in (state_map.get("outfits") or [])}
    report: list[dict] = []
    for odir in sorted((pack / "assets" / "outfits").iterdir()):
        if not odir.is_dir():
            continue
        oid = odir.name
        if only and oid not in only:
            continue
        dst = root / "outfits" / oid
        if not dry:
            dst.mkdir(parents=True, exist_ok=True)
        # 2048 档是有原稿上采样的成品，先用它；没有才退回 512
        src = odir / "stills_2048"
        canvas = 2048
        if not src.is_dir():
            src, canvas = odir / "runtime_512", 512
        copied = _copy_static(src, dst, force, dry)
        anims = _copy_anims(odir / "animations", dst, force, dry)
        meta = _write_meta(dst, oid, labels.get(oid, ""), canvas, anims, dry)
        report.append({
            "outfit": oid,
            "label": meta.get("label") or oid,
            "copied": copied,
            "poses": len(meta.get("poses") or []),
            "animations": sorted(anims),
        })
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True)
    ap.add_argument("--root", default="")
    ap.add_argument("--only", default="", help="逗号分隔的服装 id")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pack = Path(args.pack).expanduser()
    if not (pack / "assets" / "outfits").is_dir():
        raise SystemExit(f"不是总包目录：{pack}")
    if args.root:
        root = Path(args.root).expanduser()
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                                / "integrations" / "mac-worker"))
        from character_assets import character_root  # noqa: PLC0415
        root = character_root("whale")
    only = [s.strip() for s in args.only.split(",") if s.strip()]
    rows = install(pack, root, only, args.force, args.dry_run)
    if args.json:
        print(json.dumps({"root": str(root), "dry_run": args.dry_run,
                          "outfits": rows}, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            print(f"{r['outfit']:<14} {r['label']:<8} 动作 {r['poses']:>2} "
                  f"新图 {r['copied']:>3} 动画 {','.join(r['animations']) or '-'}")
        print(f"共 {len(rows)} 套，根目录 {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
