"""draft_listing.py — 让「引擎生成的草稿」出现在剪映首页草稿箱，并读回首页实际显示的名字。

## 为什么需要这一步

剪映没有「按路径打开草稿」的入口，导出面板也只存在于**已打开的草稿**里，所以整条 GUI
链路的第一跳是「在首页草稿箱点开那张草稿」。而首页草稿箱是 CEF 自绘的固定一屏，
AX 树里只有**排在最前面的一屏**（实测 8 张）卡片的标题，排在后面的草稿根本读不到、
也就点不到。

## 实测到的排序规则（剪映 11.6.0 build 490，2026-10-29）

* 首页显示哪 8 张，由草稿索引 `com.lveditor.draft/root_meta_info.json` 里
  `all_draft_store[*].tm_draft_modified`（微秒时间戳）**从新到旧**决定；
* 只改草稿目录里的 `draft_meta_info.json` **无效**（首页不读它做排序）；
* 改索引里的那条 `tm_draft_modified` + 重启剪映，草稿立刻出现在第一屏（实测：
  验收草稿 `studio_6ae20a3cb2886cd834c8b67e__r1` 48 小时前的旧戳，改戳重启后成为第 7 张卡，
  双击即进编辑器）；
* 新建目录只要文件形状对（`draft_content.json`/`draft_info.json`/`draft_meta_info.json`
  + `draft_version.json`），剪映启动时会自己扫描并写进索引（实测复制一个原生草稿目录、
  不动索引，重启后索引从 47 条涨到 50 条、首页多出那张卡）。

## 安全边界

这里会**改用户的剪映索引文件**，所以：写之前先备份成 `*.studio-bak`（只在第一次写时备份，
不会覆盖掉原始备份），只改一条记录的 `tm_draft_modified`，不动别的字段，不删条目。
`restore()` 可以把整个索引还原回备份。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

DRAFT_ROOT = Path.home() / "Movies/JianyingPro/User Data/Projects/com.lveditor.draft"
INDEX_NAME = "root_meta_info.json"
BAK_SUFFIX = ".studio-bak"


class ListingError(RuntimeError):
    pass


def index_path(root: Path | None = None) -> Path:
    return (root or DRAFT_ROOT) / INDEX_NAME


def read_index(root: Path | None = None) -> dict:
    p = index_path(root)
    if not p.is_file():
        raise ListingError("找不到剪映草稿索引：%s" % p)
    return json.loads(p.read_text(encoding="utf-8"))


def entries(idx: dict) -> list:
    return list(idx.get("all_draft_store") or [])


def find_entry(idx: dict, name: str = "", draft_path: str = "") -> dict | None:
    """按草稿名或目录找索引条目；name 允许是路径里最后一段。"""
    norm = os.path.normpath(draft_path) if draft_path else ""
    for e in entries(idx):
        if name and e.get("draft_name") == name:
            return e
        if norm and os.path.normpath(str(e.get("draft_fold_path", ""))) == norm:
            return e
    return None


def visible_titles(tree_dump: str) -> list:
    """从 `ax_tool tree` 的输出里取首页实际显示的草稿名（只有第一屏）。"""
    out = []
    for line in (tree_dump or "").splitlines():
        if "HomePageDraftTitle:" not in line:
            continue
        token = line.split("HomePageDraftTitle:", 1)[1]
        token = token.split("\t", 1)[0].strip()
        if token and token not in out:
            out.append(token)
    return out


def _backup(p: Path) -> Path | None:
    """第一次写之前备份；已有备份不覆盖（保住最原始的那份）。"""
    bak = p.with_name(p.name + BAK_SUFFIX)
    if bak.exists():
        return None
    bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    return bak


def surface(name: str = "", draft_path: str = "", root: Path | None = None,
            now_us: int | None = None, dry: bool = False) -> dict:
    """把某个草稿的修改时间顶到最新，让它进入剪映首页第一屏。

    返回 `{"name","path","before_us","after_us","backup","changed","reason"}`；
    找不到草稿时 `changed=False` 且 `reason=draft_not_indexed`——这时要先让剪映扫一次目录。
    """
    root = root or DRAFT_ROOT
    idx = read_index(root)
    e = find_entry(idx, name=name, draft_path=draft_path)
    stamp = int(now_us if now_us is not None else time.time() * 1_000_000)
    if e is None:
        return {"name": name, "path": draft_path, "before_us": None, "after_us": None,
                "backup": None, "changed": False, "reason": "draft_not_indexed"}
    before = e.get("tm_draft_modified")
    after = max(stamp, int(before or 0) + 1_000_000)
    e["tm_draft_modified"] = after
    result = {"name": e.get("draft_name"), "path": e.get("draft_fold_path"),
              "before_us": before, "after_us": after, "backup": None,
              "changed": bool(before != after), "reason": "ok"}
    if dry or not result["changed"]:
        return result
    p = index_path(root)
    bak = _backup(p)
    result["backup"] = str(bak) if bak else str(p.with_name(p.name + BAK_SUFFIX))
    p.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    # 目录里的 meta 也顺手对齐：它不决定排序，但读起来不容易误会
    meta = Path(str(e.get("draft_fold_path") or "")) / "draft_meta_info.json"
    if meta.is_file():
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
            base = m.get("draft_meta_info") if isinstance(m.get("draft_meta_info"), dict) else m
            base["tm_draft_modified"] = after
            meta.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
        except (OSError, ValueError):
            pass
    return result


def restore(root: Path | None = None) -> dict:
    """把索引还原成第一次写之前的备份（备份不存在就什么都不做）。"""
    p = index_path(root or DRAFT_ROOT)
    bak = p.with_name(p.name + BAK_SUFFIX)
    if not bak.is_file():
        return {"restored": False, "reason": "no_backup", "path": str(p)}
    p.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
    return {"restored": True, "reason": "ok", "path": str(p), "entries": len(entries(read_index(root)))}


def stale_entries(root: Path | None = None) -> list:
    """索引里指向不存在目录的条目：`[{"name","path"}]`（清理实验目录时用，别手删 JSON）。"""
    root = root or DRAFT_ROOT
    idx = read_index(root)
    gone = []
    for e in entries(idx):
        p = Path(str(e.get("draft_fold_path") or ""))
        if not p.is_dir():
            gone.append({"name": e.get("draft_name"), "path": str(p)})
    return gone


def drop_entry(name: str, root: Path | None = None) -> dict:
    """删掉一条索引记录（只用于清理自己造的实验目录条目）。"""
    root = root or DRAFT_ROOT
    p = index_path(root)
    idx = read_index(root)
    keep = [e for e in entries(idx) if e.get("draft_name") != name]
    removed = len(entries(idx)) - len(keep)
    if not removed:
        return {"removed": 0, "entries": len(keep)}
    idx["all_draft_store"] = keep
    _backup(p)
    p.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    return {"removed": removed, "entries": len(keep)}
