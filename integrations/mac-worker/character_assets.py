"""虚拟形象的素材模型：服装（身体）与表情分开存，换装只换身体。

现状是「一张 PNG 把动作、口型、眼睛、衣服全画死」，所以每加一套衣服就得
把 24 个动作重画一遍，表情也没法跨服装复用。这里把落盘约定固定下来，读图
的一方按同一套规则拼层：

    state/characters/<id>/
      character.json                 # id、名字、默认服装、动作全集、表情全集
      meta/anchors.json              # 每套服装每个动作的头部锚点（DES 坐标系）
      expressions/<expr>.png         # 跨服装共用：笑、羞、惊、恼…
      outfits/<oid>/
        meta.json                    # label / mode(baked|split) / poses / sha256
        poses/pose_<pose>_<mouth>.png    # baked：脸已经画在图上（旧素材）
        base/01_base_<mouth>.png         # baked 的站姿
        body/pose_<pose>.png             # split：只有身体，脸部区域透明
        face/<expr>.png                  # split 时的单套覆盖（可选）

拼层规则：split 服装优先用 body/ + 表情层；缺层就退回 baked 图，再缺就退回
程序化画图。任何一步缺失都不抛错，出片优先级高于素材完美。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

DES = 512
DEFAULT_POSES = ("idle", "wave", "point", "think", "happy", "shrug",
                 "surprise", "pout", "shy", "approve")
MOUTHS = ("closed", "half", "open", "round")
DEFAULT_ANCHOR = (DES // 2, int(DES * 0.30))
OUTFIT_LABELS = {
    "01_sailor": "水手裙",
    "02_maid": "经典女仆装",
    "03_casual": "鲸鱼卫衣休闲装",
    "04_winter": "冬日斗篷装",
    "05_princess": "深海公主礼服",
}


def character_root(cid: str = "whale") -> Path:
    base = os.environ.get("WHALE_ASSET_ROOT")
    if base:
        return Path(base).expanduser()
    return Path(f"~/CreatorStudio/state/characters/{cid}").expanduser()


def _read_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 素材描述坏了不该让出片挂掉
        return default


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


@dataclass
class Outfit:
    id: str
    label: str = ""
    mode: str = "baked"          # baked | split
    poses: list[str] = field(default_factory=list)
    mouths: list[str] = field(default_factory=list)
    anchors: dict = field(default_factory=dict)
    root: Path | None = None
    meta: dict = field(default_factory=dict)

    def to_public(self) -> dict:
        return {
            "id": self.id,
            "label": self.label or self.id,
            "mode": self.mode,
            "poses": list(self.poses),
            "mouths": list(self.mouths),
            "has_expression_layer": self.mode == "split",
            "anchor_count": len(self.anchors),
        }


def _scan_poses(dir_: Path, kind: str) -> tuple[list[str], list[str]]:
    """从文件名反推动作与口型：pose_wave_open.png / 01_base_closed.png。"""
    poses: set[str] = set()
    mouths: set[str] = set()
    if not dir_.is_dir():
        return [], []
    for p in sorted(dir_.glob("*.png")):
        stem = p.stem
        if kind == "base":
            # 站姿文件是 01_base_closed / 03_base_blink_open，动作只有 idle。
            poses.add("idle")
            mouths.update(t for t in stem.split("_") if t in MOUTHS)
            continue
        stem = re.sub(r"^(pose_|\d{2}_)", "", stem)
        parts = [t for t in stem.split("_") if t]
        if not parts:
            continue
        poses.add(parts[0])
        mouths.update(t for t in parts[1:] if t in MOUTHS)
    return sorted(poses), sorted(mouths)


def read_outfit(root: Path, oid: str) -> Outfit:
    odir = Path(root) / "outfits" / oid
    meta = _read_json(odir / "meta.json", {})
    body_poses, _ = _scan_poses(odir / "body", "body")
    baked_poses, baked_mouths = _scan_poses(odir / "poses", "poses")
    base_poses, base_mouths = _scan_poses(odir / "base", "base")
    mode = meta.get("mode") or ("split" if body_poses else "baked")
    poses = body_poses or sorted(set(baked_poses) | set(base_poses))
    mouths = baked_mouths or base_mouths
    anchors = dict(meta.get("anchors") or {})
    if not anchors:
        all_anchors = _read_json(Path(root) / "meta" / "anchors.json", {})
        anchors = dict(all_anchors.get(oid) or {})
    label = str(meta.get("label") or OUTFIT_LABELS.get(oid) or "")
    return Outfit(id=oid, label=label, mode=mode,
                  poses=poses, mouths=mouths, anchors=anchors,
                  root=odir if odir.is_dir() else None, meta=meta)


@dataclass
class Character:
    id: str
    name: str
    root: Path
    default_outfit: str
    poses: list[str]
    expressions: list[str]
    outfits: list[Outfit]

    def outfit(self, oid: str | None) -> Outfit | None:
        want = str(oid or self.default_outfit)
        for o in self.outfits:
            if o.id == want:
                return o
        return self.outfits[0] if self.outfits else None

    def to_public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "default_outfit": self.default_outfit,
            "poses": list(self.poses),
            "expressions": list(self.expressions),
            "outfits": [o.to_public() for o in self.outfits],
        }


def load_character(cid: str = "whale", root: Path | None = None) -> Character:
    croot = Path(root) if root else character_root(cid)
    cfg = _read_json(croot / "character.json", {})
    outfits: list[Outfit] = []
    odir = croot / "outfits"
    if odir.is_dir():
        outfits = [read_outfit(croot, p.name) for p in sorted(odir.iterdir()) if p.is_dir()]
    if not outfits and (croot / "poses").is_dir():
        outfits = [read_outfit(croot, croot.name)]  # 没分服装目录的旧素材
    exprs = sorted(p.stem for p in (croot / "expressions").glob("*.png"))
    poses = list(cfg.get("poses") or [])
    if not poses:
        seen: set[str] = set()
        for o in outfits:
            seen.update(o.poses)
        poses = sorted(seen) or list(DEFAULT_POSES)
    default = str(cfg.get("default_outfit") or (outfits[0].id if outfits else "01_sailor"))
    return Character(id=str(cfg.get("id") or cid), name=str(cfg.get("name") or "阿斯特"),
                     root=croot, default_outfit=default, poses=poses,
                     expressions=exprs, outfits=outfits)


# ---------------------------------------------------------------- 拼层

def expression_path(root: Path, outfit: str | None, expression: str | None) -> Path | None:
    """表情层：先看这套服装有没有自己的覆盖，再用共用那一套。"""
    name = str(expression or "").strip()
    if not name:
        return None
    if outfit:
        own = Path(root) / "outfits" / outfit / "face" / f"{name}.png"
        if own.is_file():
            return own
    shared = Path(root) / "expressions" / f"{name}.png"
    return shared if shared.is_file() else None


def head_anchor(outfit: Outfit | None, pose: str) -> tuple[int, int]:
    """表情层贴哪儿：服装 meta 里按动作给，没给就用统一的头顶锚点。"""
    if outfit is not None:
        for key in (pose, "default", "idle"):
            hit = (outfit.anchors or {}).get(key)
            if isinstance(hit, (list, tuple)) and len(hit) == 2:
                try:
                    return int(hit[0]), int(hit[1])
                except (TypeError, ValueError):
                    break
    return DEFAULT_ANCHOR


# ---------------------------------------------------------------- 立绘归一化
# 八套服装的母版不是同一块画布：01–05 是 1254²，06–08 是 2048²，人物在画布里的
# 占比差着近 3%（量过：0.829 ~ 0.853）。同一段 seg（width/x/y 完全一样）换一套
# 衣服，人就从 720px 变成 740px——观众读到的不是「换衣服」，是「镜头推近一档」。
#
# 归一化只改摆位用的宽度系数，不碰素材：`jianying_engine._stage_canvas` 把立绘
# 等比缩到 (w, w)，可视高度 = 内容框高度占比 × w，所以 w 乘 k 就是身高乘 k；
# x/y 仍是内容框左上角的锚点，头顶和脚底的位置照旧由设计稿决定。
# 基准取「同一动作跨服装的中位数」：大多数服装几乎不动，只把离群那几套拉回来，
# 这样整套素材看起来和导演手摆的一次一样。量不出来一律 1.0，绝不拦着出片。
_CONTENT_CACHE: dict[tuple[str, float], tuple[float, float, float] | None] = {}
_NOMINAL_CACHE: dict[tuple, float | None] = {}
_SCALE_CACHE: dict[tuple, float] = {}


def _green_box(im) -> tuple[int, int, int, int] | None:
    """绿幕之外的内容框。判定和 character._key_green 同一套阈值
    （g>90 且 g>1.3r 且 g>1.3b），这里用 numpy 整数比较向量化：逐像素走 Python
    量 8 套 × 9 动作要半分钟，而这段在每次出片开头都会跑。"""
    try:
        import numpy as np

        a = np.asarray(im)
        r = a[..., 0].astype(np.int32)
        g = a[..., 1].astype(np.int32)
        b = a[..., 2].astype(np.int32)
        bg = (g > 90) & (g * 10 > r * 13) & (g * 10 > b * 13)
        ys, xs = np.nonzero(~bg)
        if ys.size == 0:
            return None
        return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    except Exception:  # noqa: BLE001 - 没有 numpy 就退到缩略图上抠绿
        try:
            import character
            from PIL import Image as _Image

            small = im.resize((256, 256), _Image.LANCZOS)
            bb = character._key_green(small.convert("RGBA")).getbbox()
            if not bb:
                return None
            k = im.size[0] / 256.0
            return (int(bb[0] * k), int(bb[1] * k), int(bb[2] * k), int(bb[3] * k))
        except Exception:  # noqa: BLE001
            return None


def _content_ratio(path: Path) -> tuple[float, float, float] | None:
    """(高度占画布比, 中心x占宽比, 中心y占高比)。缺图坏图返回 None。"""
    try:
        key = (str(path), Path(path).stat().st_mtime)
        if key in _CONTENT_CACHE:
            return _CONTENT_CACHE[key]
        from PIL import Image

        im = Image.open(path).convert("RGB")
        cw, ch = im.size
        box = _green_box(im)
        val = None
        if box and cw > 0 and ch > 0:
            y0, y1 = box[1], box[3]
            val = ((y1 - y0) / float(ch),
                   (box[0] + box[2]) / 2.0 / float(cw),
                   (y0 + y1) / 2.0 / float(ch))
        if len(_CONTENT_CACHE) > 256:
            _CONTENT_CACHE.clear()
        _CONTENT_CACHE[key] = val
        return val
    except Exception:  # noqa: BLE001 - 量不出来就当归一化不存在
        return None


def _pose_source(odir: Path, pose: str) -> Path | None:
    """这个动作的静帧母版：优先级与 character._asset_candidates 一致。

    站姿在 base/；其余动作口型一律改在张口那张上（身体像素一帧不换），
    所以量尺寸也认 `_open` 那张。
    """
    p = _normalize_pose(pose or "idle")
    cands = [odir / "body" / f"pose_{p}.png"]
    if p in ("idle", "neutral"):
        cands += [odir / "base" / "01_base_open.png",
                  odir / "base" / "01_base_closed.png"]
    else:
        cands += [odir / "poses" / f"pose_{p}_open.png",
                  odir / "poses" / f"pose_{p}_closed.png"]
    for cand in cands:
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def _outfit_dirs(root: Path) -> list[Path]:
    """root 可能是角色根（outfits/*），也可能是单套服装目录（旧素材）。"""
    r = Path(root)
    sub = r / "outfits"
    if sub.is_dir():
        return [p for p in sorted(sub.iterdir()) if p.is_dir()]
    for kind in ("poses", "base", "body"):
        if (r / kind).is_dir():
            return [r]
    return []


def sprite_ratio(odir: Path, pose: str = "idle") -> tuple[float, float, float] | None:
    """这套服装这个动作的内容框（相对画布）。缺素材返回 None。"""
    src = _pose_source(Path(odir), pose)
    return _content_ratio(src) if src is not None else None


def nominal_ratio(root: Path, pose: str = "idle") -> float | None:
    """同一动作跨服装的内容框高度中位数：一换装尺寸不该跳，基准取中间的。"""
    try:
        dirs = _outfit_dirs(root)
        if not dirs:
            return None
        stamp = 0.0
        for od in dirs:
            try:
                stamp = max(stamp, od.stat().st_mtime)
            except OSError:
                continue
        key = (str(Path(root)), _normalize_pose(pose or "idle"), round(stamp, 3))
        if key in _NOMINAL_CACHE:
            return _NOMINAL_CACHE[key]
        vals = []
        for od in dirs:
            r = sprite_ratio(od, pose)
            if r and r[0] > 0:
                vals.append(r[0])
        val = sorted(vals)[len(vals) // 2] if vals else None
        if len(_NOMINAL_CACHE) > 128:
            _NOMINAL_CACHE.clear()
        _NOMINAL_CACHE[key] = val
        return val
    except Exception:  # noqa: BLE001
        return None


def outfit_scale(odir: Path, pose: str = "idle",
                 lo: float = 0.75, hi: float = 1.35) -> float:
    """这套服装这个动作摆位要乘的宽度系数，让人物可视高度对齐中位数。

    返回 1.0 = 不缩放：素材缺、量不出内容框、只有一套服装（中位数就是自己）
    都是 1.0。夹在 [lo, hi] 里， deformed 的素材不至于被拉到离谱的尺寸。
    """
    try:
        odir = Path(odir)
        src = _pose_source(odir, pose)
        if src is None:
            return 1.0
        own = _content_ratio(src)
        if not own or own[0] <= 0:
            return 1.0
        croot = odir.parent.parent if odir.parent.name == "outfits" else odir
        nom = nominal_ratio(croot, pose)
        if not nom:
            return 1.0
        key = (str(odir), str(pose), round(src.stat().st_mtime, 3), round(nom, 6))
        hit = _SCALE_CACHE.get(key)
        if hit is not None:
            return hit
        val = max(lo, min(hi, nom / own[0]))
        if len(_SCALE_CACHE) > 256:
            _SCALE_CACHE.clear()
        _SCALE_CACHE[key] = val
        return val
    except Exception:  # noqa: BLE001 - 归一化是锦上添花，出错就不归一化
        return 1.0


# ---------------------------------------------------------------- 导入

_ALIASES = {"stand": "idle", "base": "idle", "hello": "wave", "hi": "wave",
            "nod": "approve", "ok": "approve", "sad": "pout", "angry": "pout",
            "shock": "surprise", "wow": "surprise", "think_": "think"}


def _normalize_pose(name: str) -> str:
    key = name.strip().lower()
    return _ALIASES.get(key, key)


def _collect_source(src: Path, work: Path) -> Path:
    """zip 就解开，目录原样用；返回可扫描的根。"""
    src = Path(src).expanduser()
    if src.is_dir():
        return src
    if src.suffix.lower() == ".zip":
        work.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(src) as zf:
            for member in zf.namelist():
                if member.startswith("__MACOSX/") or member.endswith("/.DS_Store"):
                    continue
                zf.extract(member, work)
        dirs = [p for p in work.iterdir() if p.is_dir() and not p.name.startswith(".")]
        return dirs[0] if len(dirs) == 1 else work
    raise SystemExit(f"只接受目录或 zip：{src}")


def _stage_outfit(src_root: Path) -> tuple[dict[str, Path], str]:
    """把来源认成 split（body/）或 baked（poses/base/）。"""
    split: dict[str, Path] = {}
    for cand in ("body", "poses", ""):
        d = src_root / cand if cand else src_root
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.png")):
            stem = p.stem
            if stem.startswith("pose_"):
                stem = stem[5:]
            stem = stem.split("_")[0]
            if stem and stem not in split:
                split[_normalize_pose(stem)] = p
        if split:
            return split, "split" if cand == "body" or "body" in p.parts else "baked"
    return split, "baked"


def import_outfit(src: str | Path, oid: str, label: str = "", *,
                  cid: str = "whale", root: Path | None = None,
                  force: bool = False, mode: str | None = None) -> dict:
    """装一套衣服：只要求身体动作图，表情沿用共用那一套。"""
    croot = Path(root) if root else character_root(cid)
    oid = str(oid).strip()
    if not oid:
        raise SystemExit("服装 id 不能为空")
    dst = croot / "outfits" / oid
    if dst.exists() and not force:
        raise SystemExit(f"服装 {oid} 已存在，加 --force 才覆盖")
    work = croot / ".import" / oid
    if work.exists():
        shutil.rmtree(work)
    src_root = _collect_source(Path(src), work)
    staged, detected = _stage_outfit(src_root)
    if not staged:
        raise SystemExit(f"{src_root} 里没找到 pose_*.png 或 body/*.png")
    mode = mode or ("split" if detected == "split" else "baked")
    if dst.exists():
        shutil.rmtree(dst)
    sub = "body" if mode == "split" else "poses"
    (dst / sub).mkdir(parents=True, exist_ok=True)
    shas: dict[str, str] = {}
    for pose, path in staged.items():
        target = dst / sub / f"pose_{pose}.png" if mode == "split" else dst / sub / f"pose_{pose}_open.png"
        shutil.copyfile(path, target)
        shas[_rel(dst, target)] = _sha256(target)
    # baked 来源里若带口型变体，一并保留；没有也不补画。
    if mode == "baked":
        for p in sorted((src_root / "poses").glob("*.png")):
            target = dst / "poses" / p.name
            if not target.exists():
                shutil.copyfile(p, target)
                shas[_rel(dst, target)] = _sha256(target)
        if (src_root / "base").is_dir():
            shutil.copytree(src_root / "base", dst / "base", dirs_exist_ok=True)
            for p in sorted((dst / "base").glob("*.png")):
                shas[_rel(dst, p)] = _sha256(p)
    meta = {"id": oid, "label": label or oid, "mode": mode,
            "poses": sorted(staged), "sha256": shas,
            "source": str(Path(src).expanduser()), "face_baked": mode == "baked"}
    (dst / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
    if work.exists():
        shutil.rmtree(work)
    missing = sorted(set(DEFAULT_POSES) - set(staged))
    return {"outfit": oid, "mode": mode, "installed": len(staged),
            "missing_poses": missing, "root": str(dst)}


def check(root: Path | None = None, cid: str = "whale") -> dict:
    """体检：哪套服装缺动作、哪套能挂表情层、共用表情有几张。"""
    ch = load_character(cid, root)
    reference: set[str] = set()
    for o in ch.outfits:
        reference.update(o.poses)
    rows = []
    for o in ch.outfits:
        missing = sorted(reference - set(o.poses))
        rows.append({**o.to_public(), "missing_poses": missing,
                     "complete": not missing})
    return {"character": ch.id, "root": str(ch.root),
            "expressions": list(ch.expressions),
            "outfits": rows,
            "split_ready": [r["id"] for r in rows if r["mode"] == "split"]}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "check", "import"])
    ap.add_argument("--root", default="")
    ap.add_argument("--id", default="whale")
    ap.add_argument("--outfit", default="")
    ap.add_argument("--label", default="")
    ap.add_argument("--src", default="")
    ap.add_argument("--mode", default="")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).expanduser() if a.root else None
    if a.cmd == "scan":
        print(json.dumps(load_character(a.id, root).to_public(), ensure_ascii=False, indent=2))
    elif a.cmd == "check":
        print(json.dumps(check(root, a.id), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(import_outfit(a.src, a.outfit, a.label, cid=a.id, root=root,
                                       force=a.force, mode=a.mode or None),
                         ensure_ascii=False, indent=2))
