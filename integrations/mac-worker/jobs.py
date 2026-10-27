"""本机任务队列：平台自己接单、自己跑完、自己交代结果。

为什么不是直接复用 worker.py：那条通道要 docker API + S3 凭据（容器化部署用），
本机要的是「在平台里点一下就把高级视频做出来」——不依赖外部服务的本机队列。

契约：
  POST /jobs   {"kind":"paper|slides|jianying|motion|timeline", "payload":{...}}  → 202 + job
  GET  /jobs?limit=30                                                   → 最近任务
  GET  /jobs/<id>                                                       → 状态机
  GET  /jobs/<id>/log?tail=200                                          → 日志尾巴
  POST /jobs/<id>/cancel                                                → 取消
  GET  /deliverables                                                    → 成片清单

状态目录 ``$STUDIO_HOME/state/jobs/<id>/``：
  job.json（状态机，原子写）、log.txt、result.json、spec.json（提交时落盘的规格快照）。

设计边界：
  * 一次只跑 N 条（默认 2），队列里排着的不抢 CPU；
  * 失败必须留下可读原因（traceback 尾 2000 字 + 引擎自己的报错行），不吞；
  * 产物写 ``$STUDIO_HOME/deliver``（可用 DELIVER_DIR 覆盖），文件名带 job id 便于溯源；
  * 本文件只用标准库，引擎模块按需延迟导入 —— 没装 numpy/ffmpeg 的环境也能起来。
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

from config import REPO_ROOT, STUDIO_HOME

JOBS_DIR = Path(os.environ.get("JOBS_DIR", STUDIO_HOME / "state" / "jobs")).expanduser()
DELIVER_DIR = Path(os.environ.get("DELIVER_DIR", STUDIO_HOME / "deliver")).expanduser()
SCRIPTS = {
    "paper": REPO_ROOT / "deploy" / "schematic_preview.py",
}
MAX_RUNNING = int(os.environ.get("JOBS_MAX_RUNNING", "2"))
PYBIN = next((c for c in [
    os.environ.get("STUDIO_PY"),
    str(STUDIO_HOME / "runtime/integrations/vendor/MoneyPrinterTurbo/.venv/bin/python"),
    sys.executable,
] if c and Path(c).is_file()), sys.executable)
RUN_ENV = {
    **os.environ,
    "PYTHONPATH": os.environ.get("PYTHONPATH") or str(STUDIO_HOME / "runtime/lib"),
    "STUDIO_SKIP_PREFLIGHT": os.environ.get("STUDIO_SKIP_PREFLIGHT", "1"),
}
KINDS = ("paper", "slides", "jianying", "motion", "timeline")

# 成片元数据：agent 报「多长、多大」时不该靠猜，成功后就地量一次。
FFPROBE = next((c for c in [
    os.environ.get("FFPROBE"),
    shutil.which("ffprobe"),
    "/opt/homebrew/bin/ffprobe",
] if c and Path(c).is_file()), None)


# ---------------------------------------------------------------- 状态机读写

def _job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _write(job: dict) -> dict:
    job["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    d = _job_dir(job["id"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "job.json.tmp"
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(d / "job.json")
    return job


def get(job_id: str) -> dict:
    path = _job_dir(job_id) / "job.json"
    if not path.is_file():
        raise KeyError(job_id)
    return json.loads(path.read_text(encoding="utf-8"))


def list_jobs(limit: int = 30) -> list[dict]:
    if not JOBS_DIR.is_dir():
        return []
    rows = []
    for d in JOBS_DIR.iterdir():
        p = d / "job.json"
        if p.is_file():
            try:
                rows.append(json.loads(p.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    rows.sort(key=lambda j: j.get("createdAt", ""), reverse=True)
    return rows[: max(1, min(limit, 200))]


def log_text(job_id: str, tail: int = 200) -> str:
    path = _job_dir(job_id) / "log.txt"
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max(1, min(tail, 5000)):])


# ---------------------------------------------------------------- 提交 / 取消

def submit(kind: str, payload: dict | None = None) -> dict:
    kind = (kind or "").strip()
    if kind not in KINDS:
        raise ValueError(f"未知引擎：{kind}（可选 {', '.join(KINDS)}）")
    payload = payload or {}
    if kind == "paper" and not (payload.get("specPath") or payload.get("spec")):
        raise ValueError("paper 任务需要 specPath 或 spec")
    if kind in ("slides", "jianying", "motion") and not (payload.get("spec") or payload.get("specPath")):
        raise ValueError(f"{kind} 任务需要 spec（或 specPath）")
    job_id = time.strftime("%y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    d = _job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    if payload.get("spec") and not payload.get("specPath"):
        (d / "spec.json").write_text(
            json.dumps(payload["spec"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        payload = {**payload, "specPath": str(d / "spec.json")}
    job = {
        "id": job_id,
        "kind": kind,
        "status": "queued",
        "progress": 0,
        "stage": "queued",
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "payload": {k: v for k, v in payload.items() if k != "spec"},
    }
    _write(job)
    (d / "log.txt").write_text("", encoding="utf-8")
    _pump()
    return get(job_id)


def _pump() -> None:
    """把排队任务拉起来；超过并发上限就继续等，不阻塞提交方。"""
    running = [j for j in list_jobs(200) if j["status"] in ("queued", "running")]
    slots = MAX_RUNNING - sum(1 for j in running if j["status"] == "running")
    for j in [x for x in running if x["status"] == "queued"][: max(0, slots)]:
        d = _job_dir(j["id"])
        log = open(d / "log.txt", "a", encoding="utf-8")
        proc = subprocess.Popen(
            [PYBIN, str(Path(__file__).resolve()), "--run", j["id"]],
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True, env=RUN_ENV,
        )
        j.update(status="running", pid=proc.pid, startedAt=time.strftime("%Y-%m-%dT%H:%M:%S"))
        _write(j)


def cancel(job_id: str) -> dict:
    job = get(job_id)
    if job["status"] in ("succeeded", "failed", "cancelled"):
        return job
    pid = job.get("pid")
    if pid:
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    job.update(status="cancelled", endedAt=time.strftime("%Y-%m-%dT%H:%M:%S"), stage="cancelled")
    return _write(job)


# ---------------------------------------------------------------- 执行

def _progress(job: dict, pct: int, stage: str) -> None:
    job.update(progress=int(pct), stage=stage)
    _write(job)


def _run_cli(job: dict, cmd: list[str]) -> dict:
    log_path = _job_dir(job["id"]) / "log.txt"
    with open(log_path, "a", encoding="utf-8") as log:
        log.write("$ " + " ".join(cmd) + "\n")
        log.flush()
        proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, env=RUN_ENV)
    if proc.returncode != 0:
        raise RuntimeError(f"命令退出码 {proc.returncode}：{' '.join(cmd[:3])} …（详见 log）")
    return {"command": cmd[:3] + ["…"], "returncode": 0}


def _paper(job: dict, payload: dict) -> dict:
    out = Path(payload.get("out") or
               DELIVER_DIR / f"{payload.get('slug') or 'paper'}-{job['id']}.mp4").expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [PYBIN, str(SCRIPTS["paper"]), str(Path(payload["specPath"]).expanduser())]
    if payload.get("shots"):
        cmd += ["--shots", *[str(s) for s in payload["shots"]]]
    if payload.get("caps") is not None:
        cmd += ["--caps", *[str(c) for c in payload["caps"]]]
    cmd += ["--style", str(payload.get("style") or "paper"),
            "--out", str(out), "--workdir", str(_job_dir(job["id"]) / "work")]
    return {"files": [str(out)], **_run_cli(job, cmd)}


def _engine(job: dict, payload: dict) -> dict:
    """slides / jianying / motion 都是 render_xxx(payload, job_dir) 同一签名。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent / "export"))
    job_dir = _job_dir(job["id"]) / "work"
    job_dir.mkdir(parents=True, exist_ok=True)

    def progress(pct, stage="", *_: object) -> None:  # 引擎各自签名略有差别
        _progress(job, pct, stage or job.get("stage", ""))

    raw = json.loads(Path(payload["specPath"]).read_text(encoding="utf-8"))
    # 规格文件可能是 {projectId,title,...,spec:{...}} 的外壳，也可能是裸 spec；
    # 引擎要的是内层（points/narration/scenes 都在那一层）。
    spec = raw["spec"] if isinstance(raw.get("spec"), dict) else raw
    body = {k: v for k, v in payload.items() if k not in ("specPath", "spec")}
    body["spec"] = spec
    if job["kind"] == "jianying":
        from jianying_engine import render_jianying
        res = render_jianying(body, job_dir, progress)
    elif job["kind"] == "slides":
        from slides_render import render_from_payload
        res = render_from_payload(body, job_dir, progress)
    else:
        from motion_render_engine import render_motion
        res = render_motion(body, job_dir, progress)
    files = [v for k, v in (res or {}).items()
             if k.lower().endswith(("path", "mp4", "dir")) and isinstance(v, str)]
    return {"result": res, "files": files}


def _deliver(job: dict, files: list[str]) -> list[str]:
    """把引擎产出的成片收进 DELIVER_DIR：工作目录是临时的，成片要能被 films 列到。"""
    out: list[str] = []
    slug = str((job.get("payload") or {}).get("slug") or job.get("kind") or "film")
    DELIVER_DIR.mkdir(parents=True, exist_ok=True)
    for f in files:
        src = Path(str(f))
        if not src.is_file():
            continue
        if DELIVER_DIR in src.parents:
            out.append(str(src))
            continue
        dst = DELIVER_DIR / f"{slug}-{job['id']}{src.suffix}"
        shutil.copy2(src, dst)
        out.append(str(dst))
    return out


def _timeline(job: dict, payload: dict) -> dict:
    """timeline 规格 → ffmpeg 母版（headless，不启动剪映、不抢前台）。"""
    work = _job_dir(job["id"]) / "work"
    work.mkdir(parents=True, exist_ok=True)
    raw = json.loads(Path(payload["specPath"]).expanduser().read_text(encoding="utf-8"))
    # 外层 {projectId,title,spec:{...}} 的壳剥掉：timeline_render 只认内层
    spec = raw["spec"] if isinstance(raw.get("spec"), dict) else raw
    spec_path = work / "timeline.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    slug = payload.get("slug") or job["kind"]
    out = Path(payload.get("out") or DELIVER_DIR / f"{slug}-{job['id']}.mp4").expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [PYBIN, str(REPO_ROOT / "integrations" / "mac-worker" / "export" / "timeline_render.py"),
           "--spec", str(spec_path), "--out", str(out), "--json"]
    return {"files": [str(out)], "duration": spec.get("duration"), **_run_cli(job, cmd)}


def _media_info(path: str | None) -> dict:
    """ffprobe 量首个视频流的宽高与时长；量不到就什么都不加，不编造。"""
    if not path or not FFPROBE or not Path(path).is_file():
        return {}
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
        data = json.loads(out or "{}")
        stream = (data.get("streams") or [{}])[0]
        info: dict = {}
        if stream.get("width") and stream.get("height"):
            info["width"] = int(stream["width"])
            info["height"] = int(stream["height"])
        raw = ((data.get("format") or {}).get("duration") or "").strip()
        if raw:
            info["duration"] = round(float(raw), 3)
        return info
    except (subprocess.SubprocessError, ValueError, KeyError, IndexError):
        return {}


def run_job(job_id: str) -> int:
    job = get(job_id)
    job.update(status="running", startedAt=job.get("startedAt") or time.strftime("%Y-%m-%dT%H:%M:%S"))
    _progress(job, 1, f"{job['kind']}:started")
    try:
        payload = dict(job.get("payload") or {})
        if job["kind"] == "paper":
            res = _paper(job, payload)
        elif job["kind"] == "timeline":
            res = _timeline(job, payload)
        else:
            res = _engine(job, payload)
        if isinstance(res, dict) and job["kind"] != "paper":
            delivered = _deliver(job, list(res.get("files") or []))
            if delivered:
                res = {**res, "files": delivered}
            info = _media_info((res.get("files") or [None])[0])
            if info:
                res = {**res, **info}
        job.update(status="succeeded", progress=100, stage=f"{job['kind']}:done",
                   result=res, endedAt=time.strftime("%Y-%m-%dT%H:%M:%S"))
        _write(job)
        (_job_dir(job_id) / "result.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()[-2000:]
        with open(_job_dir(job_id) / "log.txt", "a", encoding="utf-8") as log:
            log.write("\n" + tb)
        job.update(status="failed", stage=f"{job['kind']}:failed",
                   error=f"{type(exc).__name__}: {exc}"[:800], traceback=tb,
                   endedAt=time.strftime("%Y-%m-%dT%H:%M:%S"))
        _write(job)
        return 1


# ---------------------------------------------------------------- 成片清单

def deliverables(limit: int = 50) -> list[dict]:
    if not DELIVER_DIR.is_dir():
        return []
    rows = []
    for p in DELIVER_DIR.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".mp4", ".wav", ".mov", ".m4a"):
            st = p.stat()
            rows.append({
                "name": p.name,
                "path": str(p),
                "bytes": st.st_size,
                "mtime": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
            })
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows[: max(1, min(limit, 500))]


def cleanup(job_id: str) -> dict:
    """删掉任务的工作目录（草稿/中间帧），保留 job.json 与 result.json。"""
    d = _job_dir(job_id)
    if (d / "work").is_dir():
        shutil.rmtree(d / "work", ignore_errors=True)
    return get(job_id)


# ---------------------------------------------------------------- 资产总览

MATERIALS_DIR = Path(os.environ.get("MATERIALS_DIR") or (STUDIO_HOME / "materials"))

_SHOT_EXT = (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov")


def _scan(root: Path, exts: tuple[str, ...], limit: int = 400) -> list[dict]:
    rows: list[dict] = []
    if not root.is_dir():
        return rows
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            st = p.stat()
            rows.append({"path": str(p), "bytes": st.st_size,
                         "mtime": time.strftime("%Y-%m-%dT%H:%M:%S",
                                                time.localtime(st.st_mtime))})
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows[: max(1, min(limit, 2000))]


def assets() -> dict:
    """平台自己管的三样东西：可渲的规格、可用的素材、已出的成片。

    不联网、不启动引擎，纯扫目录——所以它可以在面板里随时刷，
    也解释了「这条片子用的是什么素材」：所有路径都能直接喂给 submit。
    """
    specs = []
    for p in sorted((REPO_ROOT / "deploy" / "specs").glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            specs.append({"path": str(p), "title": p.stem, "engine": "?", "ok": False})
            continue
        inner = d.get("spec") if isinstance(d.get("spec"), dict) else d
        engine = d.get("engine") or inner.get("engine") or "?"
        # 规格里的 engine 不全是可提交的 kind：paper 规格走 paper 任务，
        # timeline 规格有自己的 job（_timeline），其余同名。
        kind = engine if engine in ("slides", "motion", "jianying", "timeline") else "paper"
        specs.append({"path": str(p), "title": d.get("title") or p.stem,
                      "engine": engine, "kind": kind, "ok": True})
    shots = _scan(MATERIALS_DIR / "shots", _SHOT_EXT)
    packs = sorted(p.name for p in (MATERIALS_DIR / "packs").glob("*")) \
        if (MATERIALS_DIR / "packs").is_dir() else []
    films = deliverables(200)
    return {
        "root": str(MATERIALS_DIR),
        "specs": specs,
        "shots": {"count": len(shots), "recent": shots[:60]},
        "packs": packs,
        "films": {"count": len(films), "recent": films[:60]},
    }


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "--run":
        return run_job(argv[2])
    if len(argv) >= 2 and argv[1] == "list":
        for j in list_jobs(20):
            print(f"{j['id']}  {j['kind']:<8} {j['status']:<9} {j.get('progress', 0):>3}%  {j.get('stage','')}")
        return 0
    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
