"""成片入库的收口逻辑：认领/导出拿到文件之后，把它交进库并读回落点。

worker 里这段原来内联、只能靠真机跑一遍才知道对不对，而它的失败模式很难看：
入库抛错时成片必须留在原处（不能因为台账问题把片子弄丢），但又不能当成功
回给后端（用户会以为片子在库里）。这里把「读体检证据 → 调 library.land →
失败留痕但不丢文件」拆出来，纯标准库，可直接单测。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

EVIDENCE_REL = Path("docs/probe/export-panel/runs")


def evidence_dir(job_id: str, outdir: str,
                 roots: list[str | Path] | None = None) -> Path | None:
    """按候选根目录顺序找这次任务的导出体检证据目录，找不到返回 None。

    证据由 export/jianying_export.py 写在仓库 docs 下；worker 跑在
    ~/CreatorStudio/runtime，所以除了 REPO_ROOT 还要试 out/<jobId> 的上一级，
    两处都没有就说明这轮没留证据，入库时 duration 只能是空的。
    """
    bases = [Path(r).expanduser() for r in (roots or [])]
    bases.append(Path(outdir).expanduser().parent)
    for base in bases:
        d = base / EVIDENCE_REL / str(job_id)
        if d.is_dir():
            return d
    return None


def read_evidence(job_id: str, outdir: str,
                  roots: list[str | Path] | None = None) -> dict[str, Any]:
    """读这次任务的体检记录；文件坏了就当没有，不影响入库。"""
    d = evidence_dir(job_id, outdir, roots)
    if not d:
        return {}
    cands = sorted(d.glob("*.json"))
    if not cands:
        return {}
    try:
        return json.loads(cands[0].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def land_payload(payload: dict[str, Any], job_id: str) -> dict[str, Any]:
    """payload → library.land 的字段（项目名/标题有多套来源，按优先级取）。"""
    return {
        "project": str(payload.get("libraryProjectName") or payload.get("libraryProject")
                       or payload.get("project") or "studio"),
        "project_id": str(payload.get("libraryProject") or ""),
        "title": str(payload.get("libraryTitle") or payload.get("title")
                     or payload.get("runId") or job_id),
        "platform": str(payload.get("platform") or "douyin"),
        "run_id": str(payload.get("runId") or ""),
        "draft": str(payload.get("draft") or ""),
        "draft_path": str(payload.get("draftPath") or ""),
    }


def land(video: str, payload: dict[str, Any], job_id: str, revision: int,
         outdir: str, *, roots: list[str | Path] | None = None,
         land_fn: Callable[..., dict] | None = None,
         warn: Callable[..., None] | None = None) -> dict[str, Any] | None:
    """把成片交进库；失败返回 None 并留痕，绝不吞掉文件。

    返回 None 的语义是「这轮没入库」：调用方应保留 out 里的成片并把
    libraryPath 之类字段留空，而不是伪造一个库路径。
    """
    fields = land_payload(payload, job_id)
    meta = read_evidence(job_id, outdir, roots)
    duration = (meta.get("probe") or {}).get("duration")
    extra = {"checks": meta.get("checks") or {}, "profile": meta.get("profile") or {},
             "panel": meta.get("panel") or ""}
    if land_fn is None:
        def land_fn(path, **kw):  # noqa: ANN001 - 延迟导入，避免测试拖依赖
            import library
            return library.land(path, **kw)
    try:
        return land_fn(str(video), project=fields["project"], title=fields["title"],
                       project_id=fields["project_id"], platform=fields["platform"],
                       revision=int(revision), run_id=fields["run_id"],
                       job_id=str(job_id), draft=fields["draft"],
                       draft_path=fields["draft_path"], duration=duration, meta=extra)
    except Exception as exc:  # 入库失败不能吞掉成片，但也必须留痕
        if warn is not None:
            warn("成片入库失败（%s）：%s 仍留在 %s", job_id, exc, video)
        return None
