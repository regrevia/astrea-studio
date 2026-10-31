"""明文草稿守卫：剪映 11.x 打开草稿会把引擎写的明文 JSON 换成密文。

本机实测（round 45）：引擎落盘的 `draft_info.json` / `draft_meta_info.json` 是
可读 JSON；剪映 11.6 打开并保存后，同一路径变成 base64/二进制密文（`GA2Jp0Q…`），
`json.load` 直接炸。社区结论一致：6.0 起草稿主文件 AES-256 加密，密钥在剪映进程里；
公开解密工具只覆盖 Windows 版 `videoeditor.dll`，macOS 移植版作者自己写明不用于生产
（见 docs/platform/jianying-version-adoption.md §4）。所以这里不解密，只做两件事：

1. 打开前把明文快照到剪映目录之外（默认 `com.lveditor.draft/.studio-plain/<草稿名>/`）；
2. 下次打开前，若目录已被剪映改写成密文，就把快照里的明文复制回去（密文先备份）。

剪映接受明文草稿打开这件事已经由本机行为证明（rounds 43/44），所以「快照 → 还原」
是这条链上唯一不依赖解密的能力，也是引擎重建草稿的兜底。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# 引擎会写的明文文件；剪映实际改写的只有前两个，但快照多留两个不亏。
PLAIN_FILES = (
    "draft_info.json",
    "draft_meta_info.json",
    "draft_content.json",
    "draft_version.json",
)

# 引擎自己写的行为证据，快照里必须带上（认领和重建都要读它）。
ENGINE_MARKERS = ("studio_export_meta.json", "studio_draft.json")

DRAFT_ROOT = Path.home() / "Movies/JianyingPro/User Data/Projects/com.lveditor.draft"
STORE_DIRNAME = ".studio-plain"
ENC_BACKUP_DIRNAME = ".studio-enc-backup"


def draft_dir(ref: str) -> Path:
    """草稿名或绝对路径都接受：只给名字时按剪映默认草稿根解析。"""
    path = Path(ref).expanduser()
    if path.is_absolute() or "/" in ref:
        return path
    return DRAFT_ROOT / ref


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_plaintext(path: Path) -> bool:
    """能按 UTF-8 JSON 解析才算明文——剪映的密文是 base64/二进制，会直接失败。"""
    try:
        json.loads(Path(path).read_text(encoding="utf-8"))
        return True
    except Exception:
        return False


def canonical(target: Path) -> list[Path]:
    """当前目录里还能读成 JSON 的草稿文件。"""
    return [target / name for name in PLAIN_FILES if (target / name).is_file() and is_plaintext(target / name)]


def encrypted(target: Path) -> list[Path]:
    """存在但已读不成 JSON 的草稿文件：剪映回写过的痕迹。"""
    return [target / name for name in PLAIN_FILES if (target / name).is_file() and not is_plaintext(target / name)]


def store_dir(target: Path, store: Path | None = None) -> Path:
    return Path(store).expanduser() if store else target.parent / STORE_DIRNAME / target.name


def snapshot(target: Path, store: Path | None = None) -> dict:
    """把明文草稿复制到剪映目录之外，返回清单（含 sha256，便于发现改动）。"""
    target = Path(target).expanduser()
    if not target.is_dir():
        raise FileNotFoundError(f"没有这个草稿目录：{target}")
    plain = canonical(target)
    if not plain or "draft_info.json" not in [p.name for p in plain]:
        raise ValueError(
            "主文件 draft_info.json 已经是密文，没有可存的明文：%s" % target)
    out = store_dir(target, store)
    files_dir = out / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for src in plain:
        dst = files_dir / src.name
        shutil.copy2(src, dst)
        entries.append({"name": src.name, "bytes": dst.stat().st_size, "sha256": _sha256(dst)})
    for name in ENGINE_MARKERS:
        src = target / name
        if src.is_file():
            shutil.copy2(src, files_dir / name)
            entries.append({"name": name, "bytes": src.stat().st_size, "sha256": _sha256(src)})
    manifest = {
        "draft": target.name,
        "draftDir": str(target),
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": entries,
        # 存快照时已经是密文的文件：还原时补不回来，状态里要说清楚
        "encryptedAtSnapshot": [p.name for p in encrypted(target)],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def read_manifest(target: Path, store: Path | None = None) -> dict | None:
    path = store_dir(target, store) / "manifest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def stale_files(target: Path, manifest: dict | None) -> list[str]:
    """快照之后又被改过的明文文件（sha256 对不上）：快照已经过期，还原会把新改动抹掉。

    只比「当前还是明文」的文件：被剪映改写成密文是快照存在的理由，不算过期。
    """
    if not manifest or not Path(target).is_dir():
        return []
    changed = []
    for entry in manifest.get("files") or []:
        path = Path(target) / str(entry.get("name") or "")
        if not path.is_file() or not is_plaintext(path):
            continue
        if entry.get("sha256") and _sha256(path) != entry["sha256"]:
            changed.append(path.name)
    return changed


def status(target: Path, store: Path | None = None) -> dict:
    target = Path(target).expanduser()
    manifest = read_manifest(target, store)
    changed = stale_files(target, manifest) if target.is_dir() else []
    return {
        "draft": target.name,
        "draftDir": str(target),
        "exists": target.is_dir(),
        "plain": [p.name for p in canonical(target)] if target.is_dir() else [],
        "encrypted": [p.name for p in encrypted(target)] if target.is_dir() else [],
        "hasSnapshot": bool(manifest),
        "snapshotAt": (manifest or {}).get("createdAt"),
        "restorable": bool(manifest) and target.is_dir(),
        # 快照过期：草稿在快照之后又被改过（引擎重建、手工编辑），还原前要看一眼
        "stale": bool(changed),
        "changedSinceSnapshot": changed,
    }


def restore(target: Path, store: Path | None = None, dry_run: bool = False) -> dict:
    """把快照里的明文写回草稿目录；被替换掉的密文先备份，可回滚。"""
    target = Path(target).expanduser()
    manifest = read_manifest(target, store)
    if not manifest:
        raise FileNotFoundError(f"没有快照：{store_dir(target, store)}")
    if not target.is_dir():
        raise FileNotFoundError(f"没有这个草稿目录：{target}")
    files_dir = store_dir(target, store) / "files"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = store_dir(target, store) / ENC_BACKUP_DIRNAME / stamp
    restored, backed_up = [], []
    for entry in manifest["files"]:
        if entry["name"] not in PLAIN_FILES:
            continue  # 引擎标记文件剪映不碰，不用写回
        src = files_dir / entry["name"]
        if not src.is_file():
            continue
        dst = target / entry["name"]
        if dst.is_file() and not is_plaintext(dst):
            backed_up.append(dst.name)
            if not dry_run:
                backup.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, backup / dst.name)
        if not dry_run:
            shutil.copy2(src, dst)
        restored.append(dst.name)
    return {
        "draft": target.name,
        "dryRun": dry_run,
        "restored": restored,
        "encryptedBackup": str(backup) if backed_up and not dry_run else "",
        "backedUp": backed_up,
    }


def ensure_plain(target: Path, store: Path | None = None) -> dict:
    """打开草稿前的统一入口：明文就顺带存快照，密文就从快照还原。"""
    target = Path(target).expanduser()
    if is_plaintext(target / "draft_info.json"):
        # 明文（含「只被剪映改写了一个文件」的混合态）：先存快照，下次打开前才有得还原
        return {"action": "snapshot", "detail": snapshot(target, store)}
    if read_manifest(target, store):
        return {"action": "restore", "detail": restore(target, store)}
    state = status(target, store)
    raise ValueError(f"草稿既没有完整明文也没有可用快照：{json.dumps(state, ensure_ascii=False)}")


def list_stores(root: Path | None = None) -> list[dict]:
    root = Path(root).expanduser() if root else DRAFT_ROOT
    out = []
    for path in sorted(root.glob(f"{STORE_DIRNAME}/*/manifest.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        target = root / data.get("draft", "")
        out.append({
            "draft": data.get("draft"),
            "snapshotAt": data.get("createdAt"),
            "files": len(data.get("files") or []),
            "draftExists": target.is_dir(),
            "needsRestore": bool(target.is_dir()) and bool(encrypted(target)),
        })
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="明文草稿快照/还原（对抗剪映回写加密）")
    ap.add_argument("command", choices=("status", "snapshot", "restore", "ensure", "list"))
    ap.add_argument("draft", nargs="?", default="", help="草稿名或绝对路径")
    ap.add_argument("--store", default="", help="快照目录，默认放在草稿根下的 .studio-plain/")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    store = Path(args.store) if args.store else None
    if args.command == "list":
        result = list_stores()
    else:
        if not args.draft:
            ap.error("需要草稿名或路径")
        target = draft_dir(args.draft)
        if args.command == "status":
            result = status(target, store)
        elif args.command == "snapshot":
            result = snapshot(target, store)
        elif args.command == "restore":
            result = restore(target, store, dry_run=args.dry_run)
        else:
            result = ensure_plain(target, store)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
