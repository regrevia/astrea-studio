#!/usr/bin/env python3
"""Capture web page screenshots as material for a production run.

Why shot-scraper and not playwright directly: shot-scraper keeps one browser
alive across a batch, so N pages cost roughly one cold start instead of N.
Measured on this machine (M1/M4, headless shell):
    - single page via `playwright screenshot` : 9.3s
    - two pages via `shot-scraper multi`      : 5.0s total
That difference is the whole reason this file exists.

Design notes:
  * Pure subprocess + YAML; no LLM, no vision tokens. Text-first philosophy
    from the skill's L0-L4 layering: capture is L2, deterministic.
  * Always verify the produced file is non-empty. `playwright screenshot`
    exits 0 even when the browser is missing (it just prints an error), so a
    bare returncode check is not evidence of a screenshot. shot-scraper shares
    the same launcher underneath, so we check size defensively.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SHOT_SCRAPER = os.environ.get("SHOT_SCRAPER_BIN") or shutil.which("shot-scraper") or "shot-scraper"
UVX_FALLBACK = os.environ.get("UVX_BIN") or shutil.which("uvx") or "uvx"

# A screenshot smaller than this is almost certainly an error page or a
# blank capture rather than real content (a plain white 1200x700 PNG is ~3-6KB).
MIN_BYTES = 2_000


class CaptureError(RuntimeError):
    pass


@dataclass
class CaptureSpec:
    urls: list[str]
    width: int = 1280
    height: int = 800
    wait_ms: int = 1200
    full_page: bool = False
    quality: int = 85          # JPEG quality; PNG is used when None
    extra: dict[str, Any] = field(default_factory=dict)


def _tool_cmd() -> list[str]:
    """Prefer an installed entry point; fall back to a zero-install uvx run."""
    if Path(SHOT_SCRAPER).is_file() or shutil.which(SHOT_SCRAPER):
        return [SHOT_SCRAPER]
    if shutil.which(UVX_FALLBACK):
        return [UVX_FALLBACK, "shot-scraper"]
    raise CaptureError(
        "shot-scraper not found; install with `brew install shot-scraper` or let uvx fetch it"
    )


def _write_storyboard(spec: CaptureSpec, outdir: Path) -> tuple[Path, list[Path]]:
    """Build the YAML shot-scraper multi expects and predict its output names."""
    lines: list[str] = []
    outputs: list[Path] = []
    ext = "jpg" if spec.quality else "png"
    for i, url in enumerate(spec.urls, start=1):
        out = outdir / f"shot-{i:02d}.{ext}"
        outputs.append(out)
        lines.append(f"- output: {out.as_posix()}")
        lines.append(f"  url: {url}")
        lines.append(f"  width: {spec.width}")
        lines.append(f"  height: {spec.height}")
        lines.append(f"  wait: {spec.wait_ms}")
        if spec.full_page:
            lines.append("  full_page: true")
        if spec.quality and ext == "jpg":
            lines.append(f"  quality: {spec.quality}")
        lines.append("")
    storyboard = outdir / "shots.yml"
    storyboard.write_text("\n".join(lines), encoding="utf-8")
    return storyboard, outputs


def _root_cause(blob: str) -> str:
    """Pull one actionable line out of a playwright traceback.

    shot-scraper surfaces playwright's raw stack; the useful part is the
    ``Error: ... net::ERR_...`` line, everything else is noise for a caller.
    """
    for line in reversed(blob.splitlines()):
        s = line.strip()
        if any(k in s for k in ("Error:", "net::ERR_", "Timeout", "ERR_")):
            return s[:300]
    tail = [ln.strip() for ln in blob.splitlines() if ln.strip()]
    return tail[-1][:300] if tail else "no output"


def capture(spec: CaptureSpec, outdir: Path, timeout: int = 300) -> list[Path]:
    """Capture every URL. Returns the real files that were produced.

    Raises CaptureError if any page failed or yielded an implausible file, so a
    render job never proceeds with silently missing material.
    """
    if not spec.urls:
        raise CaptureError("capture needs at least one url")

    outdir.mkdir(parents=True, exist_ok=True)
    storyboard, expected = _write_storyboard(spec, outdir)

    cnt = len(spec.urls)
    cmd = [*_tool_cmd(), "multi", str(storyboard)]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    except subprocess.TimeoutExpired:
        raise CaptureError(f"shot-scraper timed out after {timeout}s for {cnt} page(s)")
    blob = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()

    # shot-scraper returns non-zero on hard failures, but an individual page can
    # fail while the batch still exits 0, so verify the artifacts themselves.
    produced: list[Path] = []
    problems: list[str] = []
    for path in expected:
        if not path.is_file():
            problems.append(f"{path.name}: not created")
            continue
        size = path.stat().st_size
        if size < MIN_BYTES:
            problems.append(f"{path.name}: only {size}B (likely blank/error page)")
            continue
        produced.append(path)

    if problems:
        raise CaptureError(
            "screenshot capture failed: " + "; ".join(problems)
            + f" | cause: {_root_cause(blob)}"
        )
    return produced


def capture_to_materials(payload: dict[str, Any], workdir: Path) -> dict[str, Any]:
    """Worker-facing entry point: payload in, material descriptor out.

    payload["spec"]["capture"] shape:
        {"urls": ["https://..."], "width": 1280, "height": 800,
         "wait": 1200, "fullPage": false, "quality": 85}
    """
    spec_raw = payload.get("spec") or {}
    cap_raw = spec_raw.get("capture") or {}
    urls = [str(u).strip() for u in (cap_raw.get("urls") or []) if str(u).strip()]
    if not urls:
        raise CaptureError("payload.spec.capture.urls is empty")

    spec = CaptureSpec(
        urls=urls,
        width=int(cap_raw.get("width") or 1280),
        height=int(cap_raw.get("height") or 800),
        wait_ms=int(cap_raw.get("wait") or 1200),
        full_page=bool(cap_raw.get("fullPage")),
        quality=int(cap_raw.get("quality") or 0) or 85,
    )
    outdir = Path(workdir) / "shots"
    files = capture(spec, outdir)
    return {
        "images": [str(p) for p in files],
        "count": len(files),
        "urls": urls,
    }


if __name__ == "__main__":
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="capture web pages as material")
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--outdir", default=tempfile.mkdtemp(prefix="shots-"))
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=800)
    ap.add_argument("--wait", type=int, default=1200)
    ap.add_argument("--full-page", action="store_true")
    args = ap.parse_args()

    sv = CaptureSpec(
        urls=args.urls, width=args.width, height=args.height,
        wait_ms=args.wait, full_page=args.full_page,
    )
    try:
        got = capture(sv, Path(args.outdir))
    except CaptureError as exc:
        # A worker-facing tool should fail with one readable line, not a
        # playwright traceback that buries the actual cause.
        print(json.dumps({"error": str(exc)[:1500]}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({"images": [str(p) for p in got], "count": len(got)}, ensure_ascii=False))
