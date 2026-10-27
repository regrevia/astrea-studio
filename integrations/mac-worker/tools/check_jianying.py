#!/usr/bin/env python3
"""剪映链路的离线自检（不碰 GUI、不连 VectCutAPI、不改用户草稿目录）。

覆盖 B 线三个断点对应的可判定事实：
  1. 产出体检 G11：ffprobe 没输出 / 读不到时长，必须判失败（旧法 all({}) == True 全放行）
  2. 产物绑定：落点只由 (jobId, revision) 决定，已存在的修订版拒绝覆盖
  3. build_timeline 离线驱动：HTTP 换成 recorder，视频片段/独立音轨/字幕真的下发了，
     滤镜必须诚实报 unsupported + fallback=manual（VectCutAPI 没有 /add_filter）
  4. sidecar：runId/revision/draftId/sourceSpecHash/appBuild/adapterVersion/
     expectedCanvas/expectedDuration 八项齐全；同 project_name 的第二次部署另起目录

跑法：python3 tools/check_jianying.py   （退出码 0=全过，1=有失败）
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MW))
sys.path.insert(0, str(MW / "export"))

import jianying_engine as E  # noqa: E402
import jianying_export as X  # noqa: E402

FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"

_results: list[tuple[bool, str, str]] = []


def check(name: str, fn):
    try:
        detail = fn() or ""
        ok = True
    except AssertionError as exc:
        ok, detail = False, f"断言失败: {exc}"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    _results.append((ok, name, str(detail)))
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          ("  — " + str(detail)) if detail else ""))
    return ok


def require(cond, msg):
    if not cond:
        raise AssertionError(msg)


def run(*cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    require(p.returncode == 0, "%s 失败: %s" % (cmd[0], (p.stderr or "")[-300:]))
    return p.stdout


def make_media(root: Path) -> dict[str, Path]:
    """造真实媒体：ffprobe 必须能读，才能证明「体检」不是靠桩过的。"""
    root.mkdir(parents=True, exist_ok=True)
    video = root / "clip.mp4"
    image = root / "still.png"
    vo = root / "vo.m4a"
    bgm = root / "bgm.m4a"
    run(FFMPEG, "-v", "error", "-y", "-f", "lavfi",
        "-i", "testsrc=size=1280x720:rate=30:duration=6", "-pix_fmt", "yuv420p", str(video))
    run(FFMPEG, "-v", "error", "-y", "-f", "lavfi",
        "-i", "testsrc=size=900x1600:rate=1:duration=1", "-frames:v", "1", str(image))
    run(FFMPEG, "-v", "error", "-y", "-f", "lavfi",
        "-i", "sine=frequency=440:duration=2.5", "-c:a", "aac", str(vo))
    run(FFMPEG, "-v", "error", "-y", "-f", "lavfi",
        "-i", "sine=frequency=220:duration=4", "-c:a", "aac", str(bgm))
    for f in (video, image, vo, bgm):
        require(f.exists() and f.stat().st_size > 512, "素材没造出来: %s" % f)
    return {"video": video, "image": image, "vo": vo, "bgm": bgm}


# ---------------------------------------------------------------- 1) G11
def t_probe_failure_is_failure() -> str:
    expect = {"duration": 5.0, "width": 1920, "height": 1080, "fps": 30.0}
    for info, label in (({}, "空 dict"), ({"error": "boom"}, "ffprobe 报错"),
                        ({"duration": 0, "width": None, "height": 0, "fps": None},
                         "字段全空")):
        res = X.validate(info, expect)
        # 判失败有两条合法路径：probe 直接判死，或每个必需项都自己判失败
        judged_bad = (res.get("probe", {}).get("ok") is False
                      or (res and all(v["ok"] is False for v in res.values())))
        require(judged_bad, "%s 竟然被判通过: %r" % (label, res))
        require(not any(v["ok"] for v in res.values()),
                "%s 有检查项被放行: %r" % (label, res))
    return "空输出/报错/全零三种都判失败"


def t_probe_good_pass(tmp: Path, media: dict[str, Path]) -> str:
    info = X.ffprobe(media["video"])
    res = X.validate(info, {"duration": 6.0, "width": 1280, "height": 720, "fps": 30.0})
    require(all(v["ok"] for v in res.values()), "正常 mp4 体检没过: %r" % res)
    # 图片没有时长，必须 fail（旧法这里会被静默放行）
    img = X.validate(X.ffprobe(media["image"]), {"duration": 1.0, "width": 900, "height": 1600})
    require(img.get("has_duration", {}).get("ok") is False, "无时长的图被判通过: %r" % img)
    return "mp4 全项 OK；png 缺 duration 判失败"


# ---------------------------------------------------------------- 2) 产物绑定
def t_output_binding(tmp: Path) -> str:
    a_dir, a_final = X.plan_output("jobA", 2, "douyin", tmp / "out")
    b_dir, b_final = X.plan_output("jobA", 2, "douyin", tmp / "out")
    c_dir, c_final = X.plan_output("jobA", 3, "douyin", tmp / "out")
    d_dir, d_final = X.plan_output("jobB", 2, "douyin", tmp / "out")
    require((a_dir, a_final) == (b_dir, b_final), "同一任务同一修订版落点不稳定")
    require(len({a_final, c_final, d_final}) == 3, "不同修订版/任务落点撞了")
    require(str(a_final).startswith(str(tmp / "out" / "jobA" / "r2")),
            "落点没绑到 jobId/revision: %s" % a_final)
    a_dir.mkdir(parents=True, exist_ok=True)
    require(X.ensure_free(a_final) is True, "目标还不存在却判已占用")
    a_final.write_bytes(b"old")
    require(X.ensure_free(a_final) is False, "目标已存在却没拒绝覆盖")
    return "落点=%s；已存在即拒绝" % a_final.name


def t_poll_no_guess(tmp: Path) -> str:
    d = tmp / "movies"
    d.mkdir(parents=True, exist_ok=True)
    since = time.time() - 5
    require(X.poll_outputs(since, [d], timeout=0.1) == [], "空目录竟然有候选")
    for name in ("a.mp4", "b.mp4"):
        (d / name).write_bytes(b"x" * 4096)
    found = X.poll_outputs(since, [d], timeout=5)
    require(len(found) == 2, "两个新文件应返回两个候选，实际 %r" % found)
    require(X.poll_output(since, [d], timeout=5) is None, "多候选时旧接口不该挑一个")
    return "2 个候选中不猜，旧接口返回 None"


# ---------------------------------------------------------------- 3) build_timeline
class Recorder:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def post(self, path, body=None, **kw):
        self.calls.append((path, dict(body or {})))
        if path == "/create_draft":
            return {"draft_id": "DRAFT_SELFCHECK"}
        if path == "/save_draft":
            return {"draft_url": ""}
        return {}

    def of(self, path):
        return [b for p, b in self.calls if p == path]

    def paths(self):
        return [p for p, _ in self.calls]


def t_build_timeline(tmp: Path, media: dict[str, Path]) -> str:
    rec = Recorder()
    saved = {"post": E._post, "get": E._get, "catalog": E._catalog,
             "cache": dict(E._PROBE_CACHE)}
    E._post = rec.post
    E._get = lambda path, **kw: {"output": []}
    E._catalog = lambda cat: {"transitions": ["叠化"],
                              "video_scene_effects": []}.get(cat, [])
    E._PROBE_CACHE.clear()
    try:
        spec = {
            "width": 1080, "height": 1920,
            "scenes": [
                {"say": "第一屏：先给结论", "duration": 3.0,
                 "video": {"src": str(media["video"]), "in": 0.5, "out": 4.5,
                           "fit": "cover", "speed": 1.25},
                 "native": {"filter": "清晰", "transition_in": "叠化",
                            "effect": "模糊"}},
                {"say": "第二屏收尾，字幕要能按句切开", "duration": 2.0,
                 "image": str(media["image"]), "native": {"caption_chunk": 6}},
            ],
            "bgm": {"src": str(media["bgm"]), "volume": 0.3},
            "voiceover": {"src": str(media["vo"]), "start": 0.0},
        }
        built = E.build_timeline(spec, tmp, lambda pct, msg: None)
    finally:
        E._post, E._get, E._catalog = saved["post"], saved["get"], saved["catalog"]
        E._PROBE_CACHE.clear()
        E._PROBE_CACHE.update(saved["cache"])

    videos = rec.of("/add_video")
    audios = rec.of("/add_audio")
    images = rec.of("/add_image")
    texts = rec.of("/add_text")
    effects = rec.of("/add_effect")
    stats, report = built["stats"], built["report"]
    tracks = {b.get("track_name") for b in audios}

    require(len(videos) == 1, "视频片段数不对: %d" % len(videos))
    v = videos[0]
    require(abs(v["start"] - 0.5) < 1e-6, "视频源 in 没落地: %r" % v)
    require(abs((v["end"] - v["start"]) - v["duration"] * v["speed"]) < 1e-3,
            "end 不是源时间（end-start 应=duration*speed）: %r" % v)
    require(abs(v["duration"] - 3.0) < 1e-3, "目标片段时长不对: %r" % v)
    require(abs(v["scale_x"] - 1920 / 720) < 1e-3, "cover 缩放不对: %r" % v["scale_x"])
    require(v.get("transition") == "叠化", "转场没挂上: %r" % v)
    require(len(images) == 1 and abs(images[0]["end"] - images[0]["start"] - 2.0) < 1e-3,
            "静帧片段/时长不对: %r" % images)
    require(len(effects) == 1 and effects[0]["effect_type"] == "模糊", "特效没下发: %r" % effects)
    require(stats["videos"] == 1 and stats["images"] == 1, "stats 画面计数不对: %r" % stats)
    require(stats["subtitles"] >= 2, "字幕没按句切: %r" % stats)
    require(len(texts) >= 3, "文字轨太少: %d" % len(texts))

    require("audio_bgm" in tracks, "BGM 没有独立轨（会挤占配音轨）: %r" % tracks)
    require("audio_vo" in tracks, "旁白没有独立轨: %r" % tracks)
    bgm = [b for b in audios if b["track_name"] == "audio_bgm"]
    require(len(bgm) >= 2, "BGM 没循环铺满: %d 段" % len(bgm))
    covered = sum(b["duration"] for b in bgm)
    require(abs(covered - built["duration"]) < 1e-3,
            "BGM 覆盖 %.2fs ≠ 片长 %.2fs" % (covered, built["duration"]))
    require(stats["audio_tracks"] == 2, "独立音轨计数不对: %r" % stats)
    require(abs(built["duration"] - 5.0) < 1e-3, "总时长不对: %r" % built["duration"])

    filters = [r for r in report["unsupported"] if r["kind"] == "filter"]
    require(len(filters) == 1, "滤镜没进 unsupported: %r" % report["unsupported"])
    require(filters[0].get("fallback") == "manual", "滤镜没给人工兜底: %r" % filters[0])
    require("add_filter" in filters[0].get("endpoint", "") or
            "add_filter" in filters[0].get("reason", ""), "滤镜原因没说清端点缺失")
    require(report["ok"] is False and report["complete"] is False,
            "有 unsupported 却报 ok: %r" % report["counts"])
    require("skipped" not in stats, "还有 stats.skipped 暗账: %r" % list(stats))
    return ("video=%d image=%d audio 轨=%s（%d 段，覆盖 %.2fs）字幕=%d 滤镜=unsupported/manual"
            % (len(videos), len(images), sorted(tracks), len(audios), covered,
               stats["subtitles"]))


# ---------------------------------------------------------------- 4) sidecar
def t_sidecar(tmp: Path, media: dict[str, Path]) -> str:
    payload = {"runId": "run-selfcheck-001", "revision": "1",
               "appBuild": "test-build", "spec": {}}
    spec = {"width": 1080, "height": 1920, "scenes": [{"say": "x", "duration": 3.0}]}
    built = {"draft_id": "DRAFT_SELFCHECK", "duration": 5.0, "report": {"counts": {}}}
    extra = E._sidecar_extra(payload, spec, built)
    missing = [k for k in E.SIDECAR_REQUIRED if k not in extra]
    require(not missing, "sidecar 缺字段: %r" % missing)
    require(extra["revision"] == 1, "revision 没转成 int: %r" % extra["revision"])
    require(extra["expectedDuration"] == 5.0 and extra["expectedCanvas"] ==
            {"width": 1080, "height": 1920}, "期望规格不对: %r" % extra)

    src = tmp / "src_draft"
    src.mkdir(parents=True, exist_ok=True)
    (src / "draft_content.json").write_text(json.dumps({
        "id": "OLD-ID", "duration": 5000000, "fps": 30.0,
        "canvas_config": {"width": 1080, "height": 1920, "ratio": "9:16"},
        "tracks": [{"type": "video"}], "materials": {"videos": [{"id": "m1"}]},
    }, ensure_ascii=False), encoding="utf-8")
    (src / "draft_meta_info.json").write_text(json.dumps({
        "draft_name": "selfcheck", "draft_fold_path": str(src),
    }, ensure_ascii=False), encoding="utf-8")

    saved_root = E.DRAFT_ROOT
    E.DRAFT_ROOT = tmp / "draft_root"
    try:
        first = E._deploy(src, "selfcheck__r1", sidecar_extra=extra)
        second = E._deploy(src, "selfcheck__r1",
                           sidecar_extra={**extra, "revision": 2})
        require(first != second, "同 project_name 第二次部署把上一版覆盖了")
        require(first.is_dir() and second.is_dir(), "两版草稿没同时存在")
    finally:
        E.DRAFT_ROOT = saved_root

    side1 = json.loads((first / "studio_export_meta.json").read_text(encoding="utf-8"))
    side2 = json.loads((second / "studio_export_meta.json").read_text(encoding="utf-8"))
    for name, side in (("r1", side1), ("r2", side2)):
        absent = [k for k in E.SIDECAR_REQUIRED if k not in side]
        require(not absent, "%s sidecar 缺字段: %r" % (name, absent))
        require(side["draftId"] == "DRAFT_SELFCHECK", "%s draftId 不对" % name)
    require(side1["revision"] == 1 and side2["revision"] == 2,
            "修订版号串了: %r / %r" % (side1["revision"], side2["revision"]))
    require(side1["sourceSpecHash"] == side2["sourceSpecHash"], "同 spec 指纹不一致")
    require(side1["expectedDuration"] == 5.0, "expectedDuration 没落盘")
    require(side1.get("canvas", {}).get("width") == 1080, "画布没从 draft 推导出来")
    require(side1.get("tracks") == 1 and side1.get("materials") == {"videos": 1},
            "tracks/materials 没落盘: %r" % {k: side1.get(k) for k in ("tracks", "materials")})

    loaded = X.load_sidecar(first)
    require(loaded.get("runId") == "run-selfcheck-001", "导出端读不到 runId: %r" % loaded)
    require(int(loaded.get("revision")) == 1, "导出端读到的 revision 不对")
    require(float(loaded.get("expectedDuration")) == 5.0, "导出端读不到 expectedDuration")

    bad = dict(extra)
    bad.pop("sourceSpecHash")
    try:
        E._deploy(src, "selfcheck__bad", sidecar_extra=bad)
    except E.JianyingError as exc:
        require("sourceSpecHash" in str(exc), "缺字段报错没点名字段: %s" % exc)
    else:
        raise AssertionError("sidecar 缺 sourceSpecHash 竟然写成功了")
    return "两版目录 %s / %s；8 字段齐全，导出端可读" % (first.name, second.name)


def main() -> int:
    print("[剪映链路离线自检] ffmpeg=%s" % FFMPEG)
    require(Path(FFMPEG).exists() and Path(FFPROBE).exists(), "缺 ffmpeg/ffprobe，无法自检")
    with tempfile.TemporaryDirectory(prefix="jy_selfcheck_") as td:
        tmp = Path(td)
        media = make_media(tmp / "media")
        print("-- 1 产出体检（G11）")
        check("空 ffprobe 输出必须判失败", t_probe_failure_is_failure)
        check("真实 mp4 全项通过 / 图片缺时长判失败", lambda: t_probe_good_pass(tmp, media))
        print("-- 2 产物绑定（不猜最新文件、不覆盖历史修订版）")
        check("落点由 jobId+revision 决定", lambda: t_output_binding(tmp))
        check("并发候选不猜", lambda: t_poll_no_guess(tmp))
        print("-- 3 build_timeline 离线驱动（recorder 替换 HTTP）")
        check("视频片段/独立音轨/字幕/滤镜账本", lambda: t_build_timeline(tmp, media))
        print("-- 4 草稿 sidecar 与修订版隔离")
        check("8 个身份字段 + 第二次部署另起目录", lambda: t_sidecar(tmp, media))

    failed = [r for r in _results if not r[0]]
    print("\n结果: %d/%d 通过" % (len(_results) - len(failed), len(_results)))
    for _, name, detail in failed:
        print("  失败: %s — %s" % (name, detail))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
