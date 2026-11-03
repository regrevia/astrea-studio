"""草稿常驻守护：把「明文快照 → 密文还原」从被动调用变成自动发生。

为什么需要它：剪映 11.x 只要打开并保存一次，就会把引擎写的明文 JSON 换成密文
（机制见 `plain_guard.py` 顶部说明）。原来只有「平台点开草稿」这一条路径会在打开前
过一次守卫，于是有两个必然踩到的坑：

1. 用户在剪映首页直接点开草稿 → 守卫没跑过 → 文件被改写时手里没有底本；
2. 平台点开过一次之后，剪映接着保存，草稿变密文，但下一次打开前才发现——中间这段
   时间里任何读草稿的动作（列表、体检、导出归位、别的会话）都读到密文。

守护进程的做法是反过来：常驻盯住草稿根，**看到明文就存快照，看到密文就还原**。
于是「加密」这件事对上层永远是透明的——不需要谁记得去调用守卫。

三层结构（从快到慢）：
- 生成即快照：引擎写完草稿立刻存一份（`worker.py` 的 jianying_draft 分支）；
- 打开前兜底：平台打开草稿前仍走 `plain_guard.ensure_plain`（同步、必经）；
- 常驻守护：本模块，覆盖「用户自己在剪映里打开/保存」这条平台看不见的路径。

还原是安全的：密文先备份到 `.studio-enc-backup/<时间戳>/`，可回滚；快照过期
（草稿在快照之后又被改过）时不自动还原，只记事件等人决定，避免抹掉新改动。

命令行：
    python3 -m export.draft_guard --once            # 扫一遍就退出（cron/体检用）
    python3 -m export.draft_guard                   # 常驻，默认 8 秒一轮
    python3 -m export.draft_guard --once --json     # 机器可读，给界面/自检
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:  # 脚本方式跑（python3 export/draft_guard.py）时没有包上下文
    from export import plain_guard as PG
except ImportError:  # pragma: no cover - 包内导入都走这里
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from export import plain_guard as PG

#: 默认轮询间隔。剪映保存是秒级动作，8 秒足够短，又不至于让磁盘一直转。
DEFAULT_INTERVAL = 8.0

#: 文件刚被动过就先别碰：剪映可能正在写，等它写完这一轮再判断。
SETTLE_SECONDS = 5.0

#: 守护自己的流水账，一行一个事件，方便事后回答「它到底做了什么」。
JOURNAL = Path.home() / "CreatorStudio/logs/draft-guard.jsonl"

SKIP_PREFIXES = (".", "selfcheck__")


def candidates(root: Path) -> list[Path]:
    """草稿根下看起来像草稿的目录（跳过快照目录和自检产物）。"""
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or path.name.startswith(SKIP_PREFIXES):
            continue
        if any((path / name).is_file() for name in PG.PLAIN_FILES):
            out.append(path)
    return out


def _recently_touched(target: Path, window: float = SETTLE_SECONDS) -> bool:
    for name in PG.PLAIN_FILES:
        path = target / name
        try:
            if time.time() - path.stat().st_mtime < window:
                return True
        except OSError:
            continue
    return False


def _manifest_names(manifest: dict | None) -> set[str]:
    return {str(entry.get("name")) for entry in (manifest or {}).get("files") or []}


def _covers(manifest: dict | None, plain_now: list[str]) -> bool:
    """快照是否已经收下了当前所有明文文件（按名字判断，内容改动另由 sha256 兜住）。"""
    return set(plain_now) <= _manifest_names(manifest)


def inspect(target: Path, store: Path | None = None, restore: bool = True,
            dry_run: bool = False, settle: float = SETTLE_SECONDS) -> dict:
    """看一个草稿目录该做什么，并（默认）真的做掉。返回一个事件。

    action 取值：
      ok              明文且快照是最新的（或密文但快照也救不回主文件），什么都不用做
      snapshot        明文，存/刷新了快照
      would_snapshot  明文，dry_run 下本会存快照
      restore         密文，从快照还原了
      would_restore   密文，dry_run 下本会还原
      salvaged        主文件已密文，把草稿里剩余的明文抢救存快照（残缺快照）
      needs_review    密文有快照但快照过期，不自动还原，等人决定
      unrecoverable   密文且没有任何快照，只能重新生成草稿
      missing         目录不存在
      busy            文件刚被改过，这一轮跳过
    """
    target = Path(target).expanduser()
    name = target.name
    if not target.is_dir():
        return {"draft": name, "action": "missing"}

    state = PG.status(target, store)
    manifest = PG.read_manifest(target, store)
    stored = _manifest_names(manifest)
    plain_now = [p.name for p in PG.canonical(target)]

    if state["encrypted"]:
        # 主文件被剪映改写成密文：能还原就还原，救不回来就抢救剩余明文并留事件
        if "draft_info.json" not in stored:
            if plain_now and (not manifest or not _covers(manifest, plain_now)):
                if dry_run:
                    return {"draft": name, "action": "would_salvage", "files": plain_now}
                detail = PG.snapshot(target, store, partial=True)
                return {"draft": name, "action": "salvaged",
                        "files": [entry["name"] for entry in detail["files"]],
                        "encrypted": state["encrypted"],
                        "hint": "主文件已是密文，只存下了残余明文；完整还原需要重新生成草稿"}
            if manifest:
                return {"draft": name, "action": "partial",
                        "encrypted": state["encrypted"],
                        "hint": "只有残缺快照（主文件已是密文）：重新生成草稿才能恢复可编辑版本"}
            return {"draft": name, "action": "unrecoverable",
                    "encrypted": state["encrypted"],
                    "hint": "主文件已是密文且没有任何快照：重新生成草稿"}
        if state["stale"]:
            return {"draft": name, "action": "needs_review",
                    "encrypted": state["encrypted"],
                    "changedSinceSnapshot": state["changedSinceSnapshot"],
                    "hint": "快照之后草稿又被改过，自动还原会抹掉新改动"}
        if settle > 0 and _recently_touched(target, settle):
            return {"draft": name, "action": "busy"}
        if dry_run or not restore:
            return {"draft": name, "action": "would_restore"}
        detail = PG.restore(target, store)
        return {"draft": name, "action": "restore",
                "files": detail["restored"],
                "encryptedBackup": detail["encryptedBackup"]}

    # 全明文（或只剩引擎标记）：存快照，且只在有变化时刷新
    if not plain_now:
        return {"draft": name, "action": "ok"}
    if manifest and not manifest.get("partial") and _covers(manifest, plain_now) \
            and not state["changedSinceSnapshot"]:
        return {"draft": name, "action": "ok"}
    if dry_run:
        return {"draft": name, "action": "would_snapshot", "files": plain_now}
    detail = PG.snapshot(target, store)
    return {"draft": name, "action": "snapshot",
            "files": [entry["name"] for entry in detail["files"]],
            "encryptedAtSnapshot": detail.get("encryptedAtSnapshot") or []}


def scan(root: Path | None = None, store: Path | None = None, restore: bool = True,
         dry_run: bool = False, settle: float = SETTLE_SECONDS) -> dict:
    """扫一遍草稿根；返回事件表（只在有事发生时给明细，安静时 actions 为空）。"""
    root = Path(root).expanduser() if root else PG.DRAFT_ROOT
    events = []
    for target in candidates(root):
        try:
            event = inspect(target, store, restore=restore, dry_run=dry_run,
                            settle=settle)
        except Exception as exc:  # noqa: BLE001 —— 单个草稿失败不能拖垮整轮
            event = {"draft": target.name, "action": "error", "error": str(exc)[:300]}
        if event["action"] not in ("ok", "missing"):
            events.append(event)
    counts: dict[str, int] = {}
    for event in events:
        counts[event["action"]] = counts.get(event["action"], 0) + 1
    return {
        "root": str(root),
        "scanned": len(candidates(root)),
        "dryRun": dry_run,
        "counts": counts,
        "actions": events,
    }


def journal(event: dict, path: Path | None = None) -> None:
    """追加一行流水账；写日志失败不影响守护本身。"""
    record = dict(event, at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        out = Path(path).expanduser() if path else JOURNAL
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def fresh_events(events: list[dict], prev: dict[str, str]) -> list[dict]:
    """过滤掉「和上一轮同一草稿同一结论」的事件，只在状态变化时留痕。

    守护每 8 秒一轮，一个救不回来的草稿会永远停在同一个结论上；每轮都写一行，
    流水账会被同样的内容淹掉，真正的新变化反而看不见。
    """
    keep = []
    for event in events:
        key = event["draft"]
        if prev.get(key) != event["action"]:
            keep.append(event)
    return keep


def run_once(root: Path | None = None, store: Path | None = None, restore: bool = True,
             dry_run: bool = False, journal_path: Path | None = None,
             settle: float = SETTLE_SECONDS) -> dict:
    report = scan(root, store, restore=restore, dry_run=dry_run, settle=settle)
    # 只在调用方明确要流水账时记（单轮巡检）；常驻循环自己按「状态变化」过滤后记，
    # 否则每 8 秒都会把同一批老草稿重写一遍，日志就没人看了。
    if not dry_run and journal_path is not None:
        for event in report["actions"]:
            journal(event, journal_path)
    return report


def _emit(report: dict) -> None:
    counts = report.get("counts") or {}
    if not counts:
        print("草稿守护：%s 个草稿，没有需要处理的变化" % report["scanned"], flush=True)
        return
    print("草稿守护：%s 个草稿，%s" % (
        report["scanned"],
        "，".join("%s %s" % (key, value) for key, value in sorted(counts.items())),
    ), flush=True)
    for event in report["actions"]:
        extra = event.get("files") or event.get("encrypted") or []
        print("  %-13s %-28s %s" % (event["action"], event["draft"],
                                    " ".join(str(x) for x in extra)), flush=True)


def serve(interval: float = DEFAULT_INTERVAL, root: Path | None = None,
          store: Path | None = None, restore: bool = True,
          settle: float = SETTLE_SECONDS) -> int:
    """常驻循环；SIGTERM/SIGINT 干净退出，方便 launchd 管。"""
    stopping = {"flag": False}

    def _stop(signum, frame):  # noqa: ARG001
        stopping["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("草稿守护启动：%s，间隔 %ss，根 %s" % (
        started, interval, Path(root).expanduser() if root else PG.DRAFT_ROOT), flush=True)
    prev: dict[str, str] = {}
    while not stopping["flag"]:
        try:
            report = run_once(root, store, restore=restore, journal_path=None,
                              settle=settle)
            fresh = fresh_events(report["actions"], prev)
            for event in report["actions"]:
                prev[event["draft"]] = event["action"]
            for draft in [d for d in prev if d not in {e["draft"] for e in report["actions"]}]:
                prev.pop(draft, None)  # 草稿恢复正常：下次出问题要重新记
            if fresh:
                report = dict(report, actions=fresh)
                for event in fresh:
                    journal(event)
                _emit(report)
        except Exception as exc:  # noqa: BLE001 —— 守护自己不许死
            print("草稿守护出错：%s" % exc, file=sys.stderr, flush=True)
        deadline = time.time() + interval
        while not stopping["flag"] and time.time() < deadline:
            time.sleep(min(0.5, max(0.0, deadline - time.time())))
    print("草稿守护退出：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="草稿常驻守护：明文自动快照、密文自动还原")
    ap.add_argument("--once", action="store_true", help="扫一遍就退出")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    ap.add_argument("--root", default="", help="草稿根，默认剪映默认目录")
    ap.add_argument("--store", default="", help="快照目录，默认草稿根下 .studio-plain/")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-restore", action="store_true", help="只快照，不自动还原")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.root) if args.root else None
    store = Path(args.store) if args.store else None
    restore = not args.no_restore

    if args.once:
        report = run_once(root, store, restore=restore, dry_run=args.dry_run)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            _emit(report)
        return 0
    return serve(args.interval, root, store, restore)


if __name__ == "__main__":
    sys.exit(main())
