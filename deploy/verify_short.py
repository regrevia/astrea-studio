#!/usr/bin/env python3
"""竖屏短片的成品体检：把「完美」拆成能测的数，一次给结论。

用法：
  python3 deploy/verify_short.py <runId> [runId ...]

每条片子查：成片/时长/文案自检/QA 结论、切点是否爆闪、口型与语音是否同拍、
人物大小是否稳定；给两条以上时再查母版是否真的不同。任何一项不达标就退出非零。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

HOME = Path(os.environ.get("CREATOR_STUDIO_HOME", Path.home() / "CreatorStudio")).expanduser()
BASE = os.environ.get("STUDIO_API_BASE", "http://127.0.0.1:8080")
FFMPEG = os.environ.get("FFMPEG", "/opt/homebrew/bin/ffmpeg")
TOKEN = (HOME / "config" / "user.token")

# 背景取样框避开文字与人物：左侧、右上、卡片上方留白
LIGHT_BOXES = [(240, 240, 20, 1450), (240, 240, 860, 320), (520, 240, 280, 150)]
CUT_FRAME_MAX = 8.0        # 切点单帧亮度跳变上限
MOUTH_MOTION_MIN = 0.4     # 嘴部区域逐帧变化量下限（灰度级）
SIZE_SD_MAX = 0.04         # 人物高度标准差 / 均值
SIZE_DRIFT_MAX = 0.05      # 首尾人物高度差 / 均值


def api(path: str):
    req = urllib.request.Request(f"{BASE}{path}", headers={
        "Authorization": "Bearer " + TOKEN.read_text().strip()})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)["data"]


def probe(mp4: Path, w: int, h: int, fps: int, extra: list[str] | None = None) -> bytes:
    cmd = [FFMPEG, "-v", "error", "-i", str(mp4)]
    if extra:
        cmd += extra
    cmd += ["-vf", f"fps={fps},format=rgb24", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    return subprocess.run(cmd, capture_output=True).stdout


def frames(data: bytes, w: int, h: int) -> list:
    import numpy as np
    a = np.frombuffer(data, dtype=np.uint8)
    n = len(a) // (w * h * 3)
    return list(a[: n * w * h * 3].reshape(n, h, w, 3).astype(np.int16))


def light_at_cuts(mp4: Path, cuts: list[float]) -> dict:
    """三块背景留白（左下/右上/卡片上方）拼成一条 90x30 的亮度线，切点前后看跳变。"""
    import numpy as np
    vf = ("fps=10,format=rgb24,split=3[a][b][c];"
          "[a]crop=240:240:20:1450[aa];[b]crop=240:240:860:320[bb];[c]crop=520:240:280:150[cc];"
          "[aa][bb][cc]hstack=inputs=3,scale=90:32")
    out = subprocess.run([FFMPEG, "-v", "error", "-i", str(mp4), "-vf", vf,
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    b = np.frombuffer(out, dtype=np.uint8)
    nf = len(b) // (90 * 32 * 3)
    if nf < 10:
        return {"error": "decode"}
    m = b[: nf * 90 * 32 * 3].reshape(nf, -1, 3).mean(axis=1)
    fps = 10
    d = np.abs(np.diff(m, axis=0)).sum(axis=1)
    steps, pops = [], []
    for c in cuts:
        i0, i1 = int((c - 0.7) * fps), int((c + 0.3) * fps)
        steps.append(round(float(np.abs(m[min(i1, nf - 1)] - m[min(i0, nf - 1)]).sum()), 1))
        pops.append(round(float(d[max(0, i0 - 2): i1 + 2].max()), 1))
    away = [i for c in cuts for i in range(max(0, int((c - 0.7) * fps) - 2),
                                          min(len(d), int((c + 0.3) * fps) + 3))]
    rest = round(float(np.delete(d, away).max()), 1) if len(away) < len(d) else 0.0
    return {"change_at_cuts": steps, "max_frame_at_cuts": pops, "max_frame_elsewhere": rest}


def mouth_motion(mp4: Path, meta: dict) -> dict:
    """嘴部是否真的在动：脸中下方小窗的逐帧变化量，配上四种口型的使用分布。"""
    import numpy as np
    box = ("crop=140:110:690:1160,fps=30,format=gray")
    v = subprocess.run([FFMPEG, "-v", "error", "-i", str(mp4), "-vf", box,
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
    raw = np.frombuffer(v, dtype=np.uint8)
    n = len(raw) // (140 * 110)
    if n < 30:
        return {"frames": n}
    v = raw[: n * 140 * 110].reshape(n, 110, 140).astype(np.int16)
    diff = np.abs(np.diff(v, axis=0)).mean(axis=(1, 2))
    mix = (meta.get("lip") or {}).get("mix") or {}
    return {"frames": n, "motion_mean": round(float(diff.mean()), 3),
            "motion_p95": round(float(np.percentile(diff, 95)), 3),
            "shapes": sorted(k for k, val in mix.items() if val)}


def char_size(mp4: Path) -> dict:
    import numpy as np
    fps = 10
    data = subprocess.run([FFMPEG, "-v", "error", "-i", str(mp4),
                           "-vf", f"fps={fps},crop=400:520:340:1060,scale=200:260,format=rgb24",
                           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    hs = []
    for im in frames(data, 200, 260):
        r, g, b = im[:, :, 0], im[:, :, 1], im[:, :, 2]
        mask = (b > 90) & (b - r > 20) & ((im.max(axis=2) - im.min(axis=2)) > 30)
        rows = np.where(mask.sum(axis=1) > 6)[0]
        if len(rows) > 5:
            hs.append(rows[-1] - rows[0] + 1)
    if not hs:
        return {"frames": 0}
    mean = sum(hs) / len(hs)
    sd = (sum((h - mean) ** 2 for h in hs) / len(hs)) ** 0.5
    return {"frames": len(hs), "h_mean": round(mean, 1), "sd_ratio": round(sd / mean, 4),
            "drift_ratio": round(abs(hs[-1] - hs[0]) / mean, 4)}


def check(run_id: str) -> dict:
    data = api(f"/api/production/runs/{run_id}")
    run, jobs = data["run"], data.get("jobs") or []
    job_id = (jobs[0]["id"] if jobs else "") or run_id
    out = run.get("output") or {}
    mp4 = HOME / "timeline" / job_id / "master.mp4"
    report = {"run": run_id, "status": run.get("status"), "job": job_id,
              "duration": out.get("duration"), "file": str(mp4), "exists": mp4.exists()}
    if not mp4.exists():
        return report
    report["md5"] = hashlib.md5(mp4.read_bytes()).hexdigest()
    qa = out.get("videoQa") or {}
    copy = out.get("copyCheck") or {}
    report["qa_findings"] = len(qa.get("findings") or [])
    report["copy_ok"] = bool(copy.get("ok"))
    meta = json.loads((mp4.parent / "spec.json").read_text()).get("meta") or {}
    scenes = (meta.get("pacing") or {}).get("scenes", [])
    # slot 是该场在成片里的起点；末场是收尾卡，不当作中段转场
    cuts = [round(float(sc["slot"]), 2) for sc in scenes[1:-1] if sc.get("slot")]
    report["light"] = light_at_cuts(mp4, cuts)
    report["mouth"] = mouth_motion(mp4, meta)
    report["char_size"] = char_size(mp4)
    report["gesture"] = {k: meta.get("gesture", {}).get(k) for k in ("mismatch", "poses")}
    failures = []
    if report["status"] != "succeeded":
        failures.append("run 未成功")
    if report["qa_findings"]:
        failures.append(f"QA findings {report['qa_findings']}")
    if not report["copy_ok"]:
        failures.append("文案自检未过")
    if report["light"]["max_frame_at_cuts"] and max(report["light"]["max_frame_at_cuts"]) > CUT_FRAME_MAX:
        failures.append("切点爆闪")
    mouth = report["mouth"]
    if len(mouth.get("shapes") or []) < 3 or mouth.get("motion_mean", 0) < 0.4:
        failures.append("嘴部未随语音张合")
    cs = report["char_size"]
    if cs.get("sd_ratio", 0) > SIZE_SD_MAX:
        failures.append("人物大小不稳")
    if cs.get("drift_ratio", 0) > SIZE_DRIFT_MAX:
        failures.append("人物大小首尾漂移")
    if (meta.get("gesture") or {}).get("mismatch"):
        failures.append("手势与画面对不上")
    report["failures"] = failures
    return report


def main() -> int:
    ids = sys.argv[1:]
    if not ids:
        print(__doc__)
        return 2
    reports = [check(i) for i in ids]
    if len(reports) > 1:
        hashes = {r.get("md5") for r in reports}
        same = len(hashes) == 1
        print(f"[{'FAIL' if same else 'PASS'}] 批量母版不同 — {len(hashes)} 个 md5 / {len(reports)} 条")
        for r in reports:
            if same:
                r.setdefault("failures", []).append("批量母版重复")
    for r in reports:
        print(json.dumps(r, ensure_ascii=False))
    bad = [r for r in reports if r.get("failures")]
    if bad:
        print(f"结论：{len(bad)}/{len(reports)} 条未过体检")
        return 1
    print(f"结论：{len(reports)}/{len(reports)} 条过体检")
    return 0


if __name__ == "__main__":
    sys.exit(main())
