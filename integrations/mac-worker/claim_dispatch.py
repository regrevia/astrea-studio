"""认领人工导出成片的分发细节：命令行拼装、退出码归因、产物定位。

worker 里这段原来是内联的，拼错了参数或漏映射一个退出码都没有离线用例能发现，
而它恰好是「人工在剪映里点导出 → 平台认领入库」中间唯一可判定的接缝。
这里只依赖标准库，因此能在系统 python3 下直接单测，不必拖起 worker 的整套依赖。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

VIDEO_SUFFIXES = (".mp4", ".mov")

# 退出码由 export/jianying_export.py 定义；这里搬到用户能读懂的原因码上。
# 6/8/11/12 是「这轮认领不成立但可以换一步再来」，其余一律算导出失败。
FAIL_REASONS: dict[int, tuple[str, str]] = {
    6: ("export_probe_failed", "成片体检没过"),
    8: ("export_output_exists", "这一版已经有成片"),
    11: ("export_draft_revision_mismatch", "成片与草稿修订版不一致"),
    12: ("draft_media_changed", "草稿里的素材换过了"),
}


def build_argv(python: str, script: Path, *, claim_path: str, job_id: str,
               revision: int, platform: str, outdir: str, draft_path: str,
               canvas: str = "") -> list[str]:
    """把一次认领拼成命令行。

    认领必须带齐 (jobId, revision, platform, draftPath)：少任何一项都只能靠猜，
    而「猜导出目录里最新文件」在并发导出时必然拿错别人的片子。
    """
    if not str(platform or "").strip():
        raise ValueError("认领成片需要 platform：没有平台就核对不了画幅，产物也无法定名")
    argv = [
        str(python), str(script),
        "--claim", str(claim_path),
        "--job-id", str(job_id),
        "--revision", str(int(revision)),
        "--platform", str(platform),
        "--outdir", str(outdir),
        "--draft-path", str(draft_path),
    ]
    if canvas:
        argv += ["--canvas", str(canvas)]
    return argv


def failure(returncode: int, tail: str = "") -> tuple[str, str]:
    """退出码 → (原因码, 原因文本)。文本最前面带 reasonCode，后端只收文本。"""
    code, hint = FAIL_REASONS.get(int(returncode),
                                  ("jianying_export_failed", "认领没成功"))
    return code, f"认领失败 rc={int(returncode)}（{hint}）: {tail or '无输出'}"


def find_video(final_dir: str | Path) -> str | None:
    """在 (outdir/jobId/rN) 里找认领归位后的成片；多个候选取排序第一个，不猜最新。"""
    d = Path(final_dir)
    if not d.is_dir():
        return None
    for f in sorted(d.iterdir()):
        if f.is_file() and f.suffix.lower() in VIDEO_SUFFIXES:
            return str(f)
    return None


def finish(video: str, final_dir: str | Path, landed: dict | None, *,
           claimed: bool = False, remove=None) -> tuple[dict, str]:
    """归位后的收尾：回给后端的字段 + 该不该清中转目录。

    两个坑都在这里堵：入库失败时 videoPath 必须是原文件（不是空串，也不是库路径），
    中转目录只有在成片已经躺进库里时才清——反过来说，如果库目录恰好落在中转目录
    里（自定义 STUDIO_LIBRARY 时可能出现），清掉就等于删刚入库的片子，必须跳过。
    """
    raw = str(video)
    output: dict[str, Any] = {"videoPath": raw, "requiresManualExport": False}
    if claimed:
        output["claimed"] = True
    path = raw
    if not landed:
        return output, path
    path = str(landed.get("path") or raw)
    output.update({
        "videoPath": path,
        "libraryPath": landed.get("path"),
        "libraryAssetId": landed.get("assetId"),
        "md5": landed.get("md5"),
    })
    target = Path(final_dir)
    dest = Path(path)
    try:
        inside = dest.resolve().is_relative_to(target.resolve())
    except (OSError, ValueError):
        inside = False
    if target.is_dir() and not inside:
        (remove or shutil.rmtree)(target, ignore_errors=True)
    return output, path
