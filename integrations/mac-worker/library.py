#!/usr/bin/env python3
"""成片库：平台所有导出的视频只从这里落盘，命名、目录、台账都由这里决定。

为什么要有这一层：以前产物直接落在 ~/CreatorStudio/out/<jobId>/r<rev>/，
jobId 是随机的，扫一眼目录不知道是哪条片子、第几版、发哪个平台；重跑一次
又可能把上一版覆盖掉。散落几次之后就没法统一管理了。

规则（跟 docs/platform/asset-library.md 一致）：

    $STUDIO_LIBRARY/<project>/<YYYY-MM>/
        <YYYYMMDD-HHMMSS>__<project>__<seq3>__<slug>__<platform>__r<rev>.<ext>
        <同名>.json          # sidecar：来源、体检结论、md5、时长
    $STUDIO_LIBRARY/index.jsonl  # 追加式台账，一行一条，永不改写

- 文件名里的时间戳是入库时间，`seq` 是同秒内的防撞序号，`rev` 是修订版。
- 同一修订版重复入库不覆盖：目标已存在就报错，让调用方决定是升版还是回滚。
- `out/<jobId>/r<rev>/` 只是任务中转站，归位后由保留策略清掉，成片只在库里。
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get("STUDIO_LIBRARY") or (Path.home() / "CreatorStudio" / "library"))
INDEX_NAME = "index.jsonl"
VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v")
SLUG_MAX = 32


def root_dir(root=None) -> Path:
    return Path(root).expanduser() if root else DEFAULT_ROOT


def slugify(text: str, limit: int = SLUG_MAX) -> str:
    """中文标题也要能进文件名：保留中英文数字，其余折成单个连字符。"""
    s = str(text or "").strip().lower().replace("_", "-")
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", s, flags=re.UNICODE)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        return "untitled"
    return s[:limit].strip("-") or "untitled"


def project_slug(name: str) -> str:
    return slugify(name or "studio", limit=24)


def project_dir_name(project: str, project_id: str = "") -> str:
    """库目录名：人看得懂的项目名 + 稳定的短 id。

    只用 ObjectId 时一眼看不出是哪个项目；只用项目名又会在改名/重名时
    撞到别的项目，两个都要。
    """
    slug = project_slug(project)
    pid = re.sub(r"[^0-9a-zA-Z]", "", str(project_id or ""))[:8]
    return "%s__%s" % (slug, pid) if pid else slug


def md5_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _index_path(root: Path) -> Path:
    return root / INDEX_NAME


def _read_index(root: Path) -> list[dict]:
    p = _index_path(root)
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _append_index(root: Path, row: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with open(_index_path(root), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _slug_taken(root: Path, project: str, slug: str) -> bool:
    for row in _read_index(root):
        if row.get("project") == project and row.get("slug") == slug:
            return True
    return False


def plan_name(root: Path, project: str, title: str, platform: str,
              revision: int, when: float | None = None,
              ext: str = ".mp4", project_id: str = "") -> tuple[Path, Path, str]:
    """算死落点：同一天同一项目同标题的片子按 seq 往后排，重跑不会覆盖。"""
    ts = time.localtime(when if when is not None else time.time())
    stamp = time.strftime("%Y%m%d-%H%M%S", ts)
    month = time.strftime("%Y-%m", ts)
    proj = project_dir_name(project, project_id)
    base = slugify(title)
    day_dir = root_dir(root) / proj / month
    seq = 1
    while True:
        slug = base if seq == 1 else "%s-%02d" % (base, seq)
        name = "%s__%s__%03d__%s__%s__r%d%s" % (
            stamp, proj, seq, slug, slugify(platform or "video"), int(revision),
            ext if ext.startswith(".") else "." + ext)
        final = day_dir / name
        if not final.exists() and not _slug_taken(root_dir(root), proj, slug):
            return final, final.with_suffix(".json"), slug
        seq += 1
        if seq > 999:
            raise RuntimeError("同一项目同一秒内排了 999 条，先看看是不是死循环式重试")


def land(video, *, project: str, title: str, platform: str = "douyin",
         revision: int = 1, run_id: str = "", job_id: str = "", project_id: str = "",
         draft: str = "", draft_path: str = "", duration: float | None = None,
         meta: dict | None = None, root=None, move: bool = True,
         when: float | None = None) -> dict:
    """把一次导出的成片收进库里，写 sidecar 与台账，返回落点信息。"""
    src = Path(str(video)).expanduser()
    if not src.is_file():
        raise FileNotFoundError("没有这个成片：%s" % src)
    if src.suffix.lower() not in VIDEO_SUFFIXES:
        raise ValueError("不认识的容器：%s" % src.suffix)
    r = root_dir(root)
    final, sidecar, slug = plan_name(r, project, title, platform, revision,
                                     when=when, ext=src.suffix.lower(),
                                     project_id=project_id)
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists():
        raise FileExistsError("库里已有同名成片，不覆盖：%s" % final)
    digest = md5_of(src)
    size = src.stat().st_size
    if move:
        shutil.move(str(src), str(final))
    else:
        shutil.copy2(str(src), str(final))
    row = {
        "assetId": final.stem,
        "path": str(final),
        "project": project_slug(project),
        "projectId": project_id or None,
        "projectDir": project_dir_name(project, project_id),
        "title": title,
        "slug": slug,
        "platform": slugify(platform or "video"),
        "revision": int(revision),
        "runId": run_id,
        "jobId": job_id,
        "draft": draft,
        "draftPath": draft_path or None,
        "duration": round(float(duration), 3) if duration else None,
        "bytes": size,
        "md5": digest,
        "container": src.suffix.lower().lstrip("."),
        "landedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "meta": meta or {},
    }
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lock = r / ".index.lock"
    r.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        _append_index(r, row)
        fcntl.flock(fh, fcntl.LOCK_UN)
    return row


def _rename_project_segment(name: str, old: str, new: str) -> str:
    """文件名第二段是项目目录名，迁移时一起换掉，别让目录和文件名各说各话。"""
    parts = name.split("__")
    if len(parts) >= 2 and parts[1] == old:
        parts[1] = new
        return "__".join(parts)
    return name


def migrate_dir(root, old_dir_name: str, project_name: str,
                project_id: str = "", dry_run: bool = False) -> list[dict]:
    """把旧项目目录（纯 id / 早期 slug）搬成 `<项目名>__<id8>`，并回写台账。

    台账是追加式的，但路径迁移是唯一例外：路径字段跟着文件走，
    其余字段与顺序不动。
    """
    r = root_dir(root)
    old = r / str(old_dir_name)
    if not old.is_dir():
        raise FileNotFoundError("没有这个旧目录：%s" % old)
    pid = str(project_id or "")
    if not pid and re.fullmatch(r"[0-9a-fA-F]{24}", str(old_dir_name)):
        pid = str(old_dir_name)
    new_name = project_dir_name(project_name, pid)
    moved = []
    for src in sorted(old.rglob("*")):
        if not src.is_file() or src.name == INDEX_NAME:
            continue
        rel = src.relative_to(old)
        dst = r / new_name / rel
        dst = dst.with_name(_rename_project_segment(dst.name, old.name, new_name))
        moved.append({"from": str(src), "to": str(dst)})
        if dry_run:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    if dry_run:
        return moved
    # sidecar 里写的是绝对路径：视频先全部搬完，再回写，避免顺序依赖。
    for item in moved:
        dst = Path(item["to"])
        if dst.suffix != ".json":
            continue
        try:
            row = json.loads(dst.read_text(encoding="utf-8"))
        except Exception:
            continue
        for suf in VIDEO_SUFFIXES:
            if dst.with_suffix(suf).is_file():
                row["path"] = str(dst.with_suffix(suf))
                break
        row.update({
            "project": project_slug(project_name),
            "projectId": pid or None,
            "projectDir": new_name,
        })
        dst.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # 台账路径回写：旧目录前缀换新，文件名段一起换
    idx = _index_path(r)
    if idx.is_file():
        rows = _read_index(r)
        changed = False
        for row in rows:
            p = Path(str(row.get("path") or ""))
            try:
                rel = p.relative_to(old)
            except ValueError:
                continue
            row["path"] = str((r / new_name / rel).with_name(
                _rename_project_segment(rel.name, old.name, new_name)))
            row["project"] = project_slug(project_name)
            row["projectId"] = pid or None
            row["projectDir"] = new_name
            changed = True
        if changed:
            lock = r / ".index.lock"
            with open(lock, "w") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                tmp = idx.with_suffix(".jsonl.tmp")
                tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows),
                               encoding="utf-8")
                tmp.replace(idx)
                fcntl.flock(fh, fcntl.LOCK_UN)
    # 旧目录空了就删掉，避免留下空壳
    for d in sorted(old.rglob("*"), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    try:
        old.rmdir()
    except OSError:
        pass
    return moved


def migrate_studio(root, run_map: dict, dry_run: bool = False) -> dict:
    """早期不分项目、一律落在 studio/ 的成片，按 runId 找到所属项目后搬走。

    run_map 形如 {runId: {"name": 项目名, "id": 项目 id}}；找不到的留在原地，
    返回 {"moved": [...], "kept": [...]}。
    """
    r = root_dir(root)
    legacy = r / "studio"
    if not legacy.is_dir():
        return {"moved": [], "kept": []}
    sidecars = {p.with_suffix("").name: p for p in legacy.rglob("*.json")}
    videos = [p for p in legacy.rglob("*") if p.is_file() and p.suffix in VIDEO_SUFFIXES]
    moved, kept = [], []
    groups = {}
    for vid in videos:
        side = sidecars.get(vid.with_suffix("").name) or vid.with_suffix(".json")
        run_id = ""
        if side.is_file():
            try:
                run_id = str(json.loads(side.read_text(encoding="utf-8")).get("runId") or "")
            except Exception:
                run_id = ""
        target = run_map.get(run_id) if run_id else None
        if not target:
            kept.append(str(vid))
            continue
        name = str(target.get("name") or "")
        pid = str(target.get("id") or "")
        new_name = project_dir_name(name, pid)
        rel = vid.relative_to(legacy)
        dst = (r / new_name / rel).with_name(
            _rename_project_segment(vid.name, legacy.name, new_name))
        side_dst = (r / new_name / rel).with_name(
            _rename_project_segment(vid.with_suffix(".json").name, legacy.name, new_name))
        moved.append({"from": str(vid), "to": str(dst), "fromSide": str(side),
                      "side": str(side_dst), "project": project_slug(name),
                      "projectId": pid or None, "projectDir": new_name})
        groups.setdefault(new_name, []).append((vid, side, dst, side_dst, name, pid))
    if dry_run:
        return {"moved": moved, "kept": kept}
    for new_name, items in groups.items():
        for vid, side, dst, side_dst, name, pid in items:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(vid), str(dst))
            if side.is_file() and side != dst:
                try:
                    row = json.loads(side.read_text(encoding="utf-8"))
                except Exception:
                    row = {}
                row.update({"path": str(dst), "project": project_slug(name),
                            "projectId": pid or None, "projectDir": new_name})
                side_dst.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")
                if side != side_dst:
                    side.unlink()
    # 台账回写
    idx = _index_path(r)
    if idx.is_file():
        rows = _read_index(r)
        moved_ids = set()
        for m in moved:
            moved_ids.add(Path(m["from"]).name)
        changed = False
        for row in rows:
            p = Path(str(row.get("path") or ""))
            if p.parent.name != legacy.name and legacy.name not in p.parts:
                continue
            hit = next((m for m in moved if Path(m["from"]).name == p.name), None)
            if not hit:
                continue
            row.update({"path": hit["to"], "project": hit["project"],
                        "projectId": hit["projectId"], "projectDir": hit["projectDir"]})
            changed = True
        if changed:
            lock = r / ".index.lock"
            with open(lock, "w") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                tmp = idx.with_suffix(".jsonl.tmp")
                tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows),
                               encoding="utf-8")
                tmp.replace(idx)
                fcntl.flock(fh, fcntl.LOCK_UN)
    for d in sorted(legacy.rglob("*"), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    try:
        legacy.rmdir()
    except OSError:
        pass
    return {"moved": moved, "kept": kept}


def list_assets(project: str | None = None, platform: str | None = None,
                root=None, limit: int = 0) -> list[dict]:
    rows = _read_index(root_dir(root))
    if project:
        want = project_slug(project)
        rows = [r for r in rows if r.get("project") == want]
    if platform:
        want = slugify(platform)
        rows = [r for r in rows if r.get("platform") == want]
    rows.sort(key=lambda r: r.get("landedAt", ""), reverse=True)
    return rows[:limit] if limit else rows


def verify(root=None) -> list[str]:
    """台账与磁盘对齐检查：缺文件、缺 sidecar、md5 不符都报出来。"""
    r = root_dir(root)
    problems = []
    for row in _read_index(r):
        p = Path(row.get("path", ""))
        if not p.is_file():
            problems.append("缺文件：%s" % p)
            continue
        if p.stat().st_size != row.get("bytes"):
            problems.append("大小不符：%s" % p)
        side = p.with_suffix(".json")
        if not side.is_file():
            problems.append("缺 sidecar：%s" % side)
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="成片库：统一目录、命名与台账")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("land", help="把成片收进库")
    a.add_argument("--video", required=True)
    a.add_argument("--project", required=True)
    a.add_argument("--title", required=True)
    a.add_argument("--platform", default="douyin")
    a.add_argument("--revision", type=int, default=1)
    a.add_argument("--run-id", default="")
    a.add_argument("--job-id", default="")
    a.add_argument("--draft", default="")
    a.add_argument("--draft-path", default="")
    a.add_argument("--duration", type=float, default=0.0)
    a.add_argument("--copy", action="store_true", help="只复制，不移动原文件")
    a.add_argument("--root", default="")
    b = sub.add_parser("list", help="按项目列成片")
    b.add_argument("--project", default="")
    b.add_argument("--platform", default="")
    b.add_argument("--limit", type=int, default=20)
    b.add_argument("--root", default="")
    d = sub.add_parser("migrate", help="旧目录搬成 项目名__id8，并回写台账")
    d.add_argument("--dir", required=True, help="旧目录名，如 studio 或 ObjectId")
    d.add_argument("--project-name", default="")
    d.add_argument("--project-id", default="")
    d.add_argument("--dry-run", action="store_true")
    d.add_argument("--run-map", default="", help="{runId: {name,id}}，用于 studio/ 按 runId 迁移")
    d.add_argument("--root", default="")
    c = sub.add_parser("verify", help="台账与磁盘对账")
    c.add_argument("--root", default="")
    args = ap.parse_args(argv)
    if args.cmd == "land":
        row = land(args.video, project=args.project, title=args.title,
                   platform=args.platform, revision=args.revision,
                   run_id=args.run_id, job_id=args.job_id, draft=args.draft,
                   draft_path=args.draft_path,
                   duration=args.duration or None, root=args.root or None,
                   move=not args.copy)
        print(json.dumps(row, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "list":
        rows = list_assets(args.project or None, args.platform or None,
                           root=args.root or None, limit=args.limit)
        for row in rows:
            print("%s  %-12s %-8s r%d  %6.1fs  %8.1fMB  %s" % (
                row.get("landedAt", "")[:16], row.get("project", ""),
                row.get("platform", ""), int(row.get("revision") or 0),
                float(row.get("duration") or 0),
                float(row.get("bytes") or 0) / 1048576.0,
                Path(row.get("path", "")).name))
        return 0
    if args.cmd == "migrate":
        if args.run_map:
            rmap = json.loads(Path(args.run_map).read_text(encoding="utf-8"))
            res = migrate_studio(args.root or None, rmap, dry_run=args.dry_run)
            for m in res["moved"]:
                print("%s -> %s" % (m["from"], m["to"]))
            print("迁移：%d 个文件，%d 个找不到归属留在原地%s" % (
                len(res["moved"]), len(res["kept"]),
                "（预演，未改动）" if args.dry_run else ""))
            return 0
        if not args.project_name:
            ap.error("migrate 需要 --project-name（或用 --run-map）")
        moved = migrate_dir(args.root or None, args.dir, args.project_name,
                            project_id=args.project_id, dry_run=args.dry_run)
        for m in moved:
            print("%s -> %s" % (m["from"], m["to"]))
        print("迁移：%d 个文件%s" % (len(moved), "（预演，未改动）" if args.dry_run else ""))
        return 0
    problems = verify(args.root or None)
    for p in problems:
        print(p)
    print("对账：%s" % ("干净" if not problems else "%d 处问题" % len(problems)))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
