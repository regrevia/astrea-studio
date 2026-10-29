"""version_evidence.py — 版本适配置信度的**实证台账**。

`jianying_versions.py` 的采纳规则是「抄本机同主版本草稿的 schema」，可它在真机上拿不到
证据：剪映 11.x 打开引擎写的明文草稿后，**会把草稿回写成自己的加密原生格式**
（`draft_info.json` 变成一段 base64 二进制，`draft_meta_info.json` 也是），版本字段一起
被加密，读不出来了。于是「同主版本参考草稿」在 11.x 上永远为空，状态一直停在 `template`。

本模块补的是**另一种实证**：行为实证（cinematic）。判据是

    引擎写的明文草稿（带 studio_export_meta.json 侧车）
        → 剪映打开它
        → 回写成非明文原生格式（加密）
        ⇒ 这个版本的剪映**接受了**我们这套模板 schema

它比「版本号长得一样」更强：版本号相同只说明对方写过同样的版本块，而「打开+回写」说明
真机上的剪映解析了我们的整个 `draft_content.json` 并成功重存。台账落盘在
`docs/probe/version-evidence/`，每条一个 JSON，附一个 `ledger.jsonl` 汇总。

CLI：
    python3 version_evidence.py record --draft <草稿目录>
    python3 version_evidence.py status
    python3 version_evidence.py scan     # 扫草稿目录，把新发现的实证记上
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

MW_ROOT = Path(__file__).resolve().parent
REPO = MW_ROOT.parents[1]
EVIDENCE_DIR = REPO / "docs/probe/version-evidence"
LEDGER = EVIDENCE_DIR / "ledger.jsonl"
DEFAULT_DRAFT_ROOT = Path.home() / "Movies/JianyingPro/User Data/Projects/com.lveditor.draft"
ENGINE_MARKERS = ("studio_export_meta.json", "studio_draft.json")
DEFAULT_APP = Path("/Applications/VideoFusion-macOS.app")


def _major(version: Any) -> int | None:
    text = str(version or "").strip().split(".")[0]
    return int(text) if text.isdigit() else None


def read_app_info(app_path: os.PathLike | str = DEFAULT_APP) -> dict[str, Any]:
    """从 Info.plist 读剪映的版本与 build（不执行 App，不需要授权）。"""
    import plistlib

    plist = Path(app_path) / "Contents/Info.plist"
    if not plist.is_file():
        return {"path": str(app_path), "version": None, "build": None}
    try:
        with plist.open("rb") as fh:
            info = plistlib.load(fh)
    except Exception:
        return {"path": str(app_path), "version": None, "build": None}
    return {"path": str(app_path), "version": info.get("CFBundleShortVersionString"),
            "build": info.get("CFBundleVersion")}


def classify(draft_dir: os.PathLike | str) -> str:
    """草稿现在是「引擎明文」还是「剪映原生（加密）」。

    * `plaintext`：draft_content.json 是合法 JSON，引擎/人写的那种；
    * `native`：draft_info.json 解析不出 JSON（剪映回写后的加密二进制），或目录里没有
      draft_content.json 只有 native 四件套；
    * `missing`：连 draft_info.json 都没有。
    """
    d = Path(draft_dir)
    info = d / "draft_info.json"
    content = d / "draft_content.json"
    if not info.is_file() and not content.is_file():
        return "missing"
    target = info if info.is_file() else content
    try:
        raw = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "native"
    if not raw.lstrip().startswith(("{", "[")):
        return "native"
    try:
        json.loads(raw)
    except ValueError:
        return "native"
    return "plaintext"


def fingerprint(draft_dir: os.PathLike | str) -> dict[str, Any]:
    """给草稿留一份可比的指纹：形状 + 版本块 + 内容摘要。"""
    d = Path(draft_dir)
    kind = classify(d)
    out: dict[str, Any] = {"draft": d.name, "kind": kind, "path": str(d)}
    for name in ("draft_info.json", "draft_content.json", "draft_meta_info.json"):
        p = d / name
        if not p.is_file():
            continue
        data = p.read_bytes()
        out.setdefault("files", {})[name] = {
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest()[:16],
        }
    if kind == "plaintext":
        try:
            info = json.loads((d / "draft_info.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        plat = info.get("platform") or {}
        out.update({"version": info.get("version"), "new_version": info.get("new_version"),
                    "platform": plat, "keys": len(info)})
    out["engineMarkers"] = [m for m in ENGINE_MARKERS if (d / m).is_file()]
    return out


def evidence_dir() -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    return EVIDENCE_DIR


def read_ledger() -> list[dict[str, Any]]:
    if not LEDGER.is_file():
        return []
    rows = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _append_ledger(entry: dict[str, Any]) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def observe(draft_dir: os.PathLike | str, app_path: os.PathLike | str = DEFAULT_APP,
            note: str = "") -> dict[str, Any]:
    """把一次「剪映接受了这张引擎草稿」的观察整理成台账条目（不落盘）。"""
    fp = fingerprint(draft_dir)
    app = read_app_info(app_path)
    verdict = "no_evidence"
    reason = ""
    if not fp["engineMarkers"]:
        reason = "不是引擎写的草稿（缺 studio_export_meta.json 一类侧车），不作数"
    elif fp["kind"] == "native":
        verdict = "accepted_by_app"
        reason = "引擎明文草稿已被剪映回写成原生加密格式：这版剪映解析并重存成功"
    elif fp["kind"] == "plaintext":
        reason = "还是明文：剪映还没打开过它，这次不能当接受证据"
    else:
        reason = "草稿文件缺失"
    return {
        "observedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "draft": fp["draft"],
        "verdict": verdict,
        "reason": reason,
        "app": {"version": app.get("version"), "build": app.get("build"),
                "major": _major(app.get("version")), "path": app.get("path")},
        "after": fp,
        "note": note,
    }


def record(draft_dir: os.PathLike | str, app_path: os.PathLike | str = DEFAULT_APP,
           note: str = "") -> dict[str, Any]:
    """观察 + 落盘（同草稿同版本只记一次，避免台账被刷成噪音）。"""
    entry = observe(draft_dir, app_path=app_path, note=note)
    if entry["verdict"] != "accepted_by_app":
        return entry
    for old in read_ledger():
        if (old.get("draft") == entry["draft"]
                and (old.get("app") or {}).get("version") == entry["app"]["version"]):
            return old
    entry["id"] = hashlib.sha256(
        ("%s|%s|%s" % (entry["draft"], entry["app"]["version"],
                       entry["after"].get("files", {}))).encode("utf-8")).hexdigest()[:12]
    evidence_dir()
    out = EVIDENCE_DIR / ("%s__%s.json" % (entry["draft"], entry["id"]))
    out.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        entry["file"] = str(out.relative_to(REPO))
    except ValueError:  # 台账被指到仓库外（测试/自检用临时目录）
        entry["file"] = str(out)
    _append_ledger(entry)
    return entry


def accepted(app_version: Any = None, ledger: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """该主版本上「被剪映接受过」的实证条目（不带 version 就返回全部）。"""
    rows = ledger if ledger is not None else read_ledger()
    major = _major(app_version)
    out = []
    for e in rows:
        if e.get("verdict") != "accepted_by_app":
            continue
        if major is not None and (e.get("app") or {}).get("major") != major:
            continue
        out.append(e)
    return out


def status_report(app_version: Any = None, ledger: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """给 jianying_versions.support_report 用的实证摘要。"""
    rows = accepted(app_version, ledger=ledger)
    major = _major(app_version)
    if not rows:
        return {"status": "none", "major": major, "count": 0, "entries": [],
                "note": "这台机器上还没有同主版本的行为实证"}
    latest = rows[-1]
    return {"status": "accepted", "major": major, "count": len(rows),
            "entries": [{"draft": e.get("draft"),
                         "appVersion": (e.get("app") or {}).get("version"),
                         "file": e.get("file")} for e in rows],
            "note": "剪映 %s 打开并回写过本机引擎草稿（%s），模板 schema 已被这版接受"
                    % ((latest.get("app") or {}).get("version"), latest.get("draft"))}


def scan(draft_root: os.PathLike | str = DEFAULT_DRAFT_ROOT,
         app_path: os.PathLike | str = DEFAULT_APP) -> list[dict[str, Any]]:
    """扫草稿目录，把「引擎明文 → 原生」的草稿全部记进台账。"""
    root = Path(draft_root)
    if not root.is_dir():
        return []
    fresh = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        entry = observe(d, app_path=app_path)
        if entry["verdict"] != "accepted_by_app":
            continue
        seen = any(o.get("draft") == entry["draft"]
                   and (o.get("app") or {}).get("version") == entry["app"]["version"]
                   for o in read_ledger())
        if seen:
            continue
        fresh.append(record(d, app_path=app_path))
    return fresh


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    rec = sub.add_parser("record")
    rec.add_argument("--draft", required=True)
    rec.add_argument("--note", default="")
    sub.add_parser("status")
    sub.add_parser("scan")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "record":
        print(json.dumps(record(args.draft, note=args.note), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "scan":
        fresh = scan()
        print(json.dumps({"new": [e["draft"] for e in fresh], "total": len(read_ledger())},
                         ensure_ascii=False))
        return 0
    info = read_app_info()
    rep = status_report(info.get("version"))
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print("剪映 %s（build %s）实证：%s 条 — %s"
              % (info.get("version"), info.get("build"), rep["count"], rep["note"]))
    return 0 if rep["count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
