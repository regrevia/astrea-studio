#!/usr/bin/env python3
"""deliver.py — 母版 → 各平台合规成片。纯 ffmpeg，零 GUI、零焦点占用。

为什么有这一层
--------------
剪映导出面板那条路（``jianying_export.py``）能出片，但它有硬约束：
一次只能导一个规格、必须有人机交互、分辨率只能按草稿比例缩、而且要抢前台。
平台化之后我们要的是「**一条母版，一次跑完 N 个平台**」，这件事 ffmpeg 天生能做。

分工固定为：
    渲染层（slides_render / motion_render / render_mpt / 外部导入） 决定「故事怎么讲」
    交付层（本文件）                                                 决定「每个平台要什么规格」
    剪映面板（jianying_export.py）                                   只作为人工精修通道

三者共用同一个平台档案 ``platform_profiles.py``，所以「B 站要 1920x1080@30 mp4」
只有一处定义，不会两边漂移。

和剪映面板的关键差异：面板只能沿着草稿短边等比缩放，比例是死的；
ffmpeg 可以任意重采样，所以横母版出竖版成片是可行的——但**比例转换必然要取舍**，
因此这里显式提供三种 ``fit`` 并写清楚各自代价，绝不偷偷拉伸。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import platform_profiles as pp  # noqa: E402

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")

# 母版→目标的横向重排方式。冒号后面是写给用户看的代价说明。
FIT_MODES = {
    "blur": "上下/左右补模糊背景（最常用：竖化横片不丢画面，平台观感接近原生）",
    "contain": "补黑边（最快最稳，画面不丢但黑边明显）",
    "crop": "居中裁切（满屏无黑边，但会切掉画面边缘，只适合主体居中的素材）",
}
DEFAULT_FIT = "blur"

AUDIO_BITRATE = "128k"
AUDIO_RATE = 48000

# 码率档 → 相对系数的换算基准（bit / (像素·帧)）。0.08 ≈ 1080p30 的 5 Mbps，
# 和 B 站/YouTube 对 1080p 的推荐上传码率同量级；平台会二次转码，
# 再往上加只增体积不改观感。
_BPP_BASE = 0.08
_BITRATE_FACTOR = {"更低": 0.7, "推荐": 1.0, "更高": 1.35, "自定义": 1.0}


# ------------------------------------------------------------------ 探测/校验
def probe(path) -> dict:
    """ffprobe 读数。ffprobe 不在 PATH 或文件不是视频时抛 RuntimeError。"""
    path = Path(path)
    if not path.is_file():
        raise RuntimeError(f"母版不存在: {path}")
    cmd = [
        FFPROBE, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe 失败({r.returncode}): {r.stderr.strip()[-300:]}")
    info = json.loads(r.stdout or "{}")
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if v is None:
        raise RuntimeError(f"没有视频流: {path}")

    def _fps(stream) -> float:
        num, _, den = (stream.get("avg_frame_rate") or "0/1").partition("/")
        try:
            n, d = float(num), float(den or 1)
            return round(n / d, 4) if d else 0.0
        except (TypeError, ValueError):
            return 0.0

    return {
        "path": str(path),
        "width": int(v.get("width") or 0),
        "height": int(v.get("height") or 0),
        "fps": _fps(v),
        "vcodec": v.get("codec_name") or "",
        "pix_fmt": v.get("pix_fmt") or "",
        "acodec": (a or {}).get("codec_name") or "",
        "duration": float((info.get("format") or {}).get("duration") or 0.0),
        "size": int(os.path.getsize(path)),
    }


def _even(n: float) -> int:
    """h264 要求宽高是偶数，向下取偶，最小 2。"""
    v = int(n) // 2 * 2
    return max(2, v)


def target_dims(profile: "pp.Profile", master_w: int, master_h: int):
    """平台目标分辨率，但**绝不放大**。

    母版短边比目标短时（比如 720p 母版要投 B 站 1080p），按短边等比降到母版能给的
    最大尺寸：放大插值只会让文件变大、观感不会变好，而且平台还会再压一次。
    返回 ((w, h), 说明)。
    """
    tw, th = profile.width, profile.height
    m_short, t_short = min(master_w, master_h), min(tw, th)
    if m_short >= t_short:
        return (tw, th), f"按平台档案 {tw}x{th}"
    scale = m_short / float(t_short)
    w, h = _even(tw * scale), _even(th * scale)
    return (w, h), (
        f"母版短边 {m_short}px < 平台要求 {t_short}px：降到 {w}x{h}，"
        "不放大（放大只增体积不改观感，平台反正要二次转码）"
    )


def bitrate_bps(width: int, height: int, fps: float, preset: str) -> int:
    """按像素·帧推算目标码率，再乘平台档案的「更低/推荐/更高」系数。"""
    factor = _BITRATE_FACTOR.get(preset, 1.0)
    bps = width * height * (fps or 30.0) * _BPP_BASE * factor
    return int(max(1_000_000, min(bps, 80_000_000)))


def build_filter(width: int, height: int, fps: float, fit: str) -> str:
    """生成完整的 filter_complex 图，输出标签固定为 [v]。

    比例不一致时三种 fit 的展开式都在这里；模糊垫底那档需要 split/overlay，
    所以统一用「中间标签 + 统一后处理」的写法，让 fps/像素格式永远作用在最终帧上。
    """
    if fit not in FIT_MODES:
        raise ValueError(f"未知 fit {fit!r}，可选 {sorted(FIT_MODES)}")
    fps_expr = f"fps={fps:g}" if fps else "null"
    post = f"{fps_expr},setsar=1,format=yuv420p"
    if fit == "contain":
        return (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black[pg];"
            f"[pg]{post}[v]"
        )
    if fit == "crop":
        return (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},{post}[v]"
        )
    # blur：一路放大裁满做背景并模糊，一路等比缩放到装得下，叠在中间。
    # 背景**不压暗**：yuv420p 是 limited range（黑=16），任何负向亮度调整都会把
    # 暗部压到 0，超出有效范围，播放器上会看到色偏/绿边。模糊本身已经足够弱化背景。
    return (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},gblur=sigma=40[bgb];"
        f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2[ov];"
        f"[ov]{post}[v]"
    )


@dataclass
class Plan:
    master: Path
    dst: Path
    profile: "pp.Profile"
    width: int
    height: int
    fps: float
    fit: str
    vbitrate: int
    has_audio: bool
    notes: list = field(default_factory=list)

    @property
    def filtergraph(self) -> str:
        return build_filter(self.width, self.height, self.fps, self.fit)

    def argv(self) -> list:
        g = self.filtergraph  # 已经带 [v] 输出标签
        cmd = [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(self.master),
        ]
        if not self.has_audio:
            cmd += ["-f", "lavfi", "-i",
                    f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_RATE}"]
        cmd += [
            "-filter_complex", g, "-map", "[v]",
            "-c:v", "libx264", "-preset", "medium", "-profile:v", "high",
            "-pix_fmt", "yuv420p",
            "-b:v", str(self.vbitrate),
            "-maxrate", str(int(self.vbitrate * 1.5)),
            "-bufsize", str(int(self.vbitrate * 2)),
            "-movflags", "+faststart",
        ]
        if self.has_audio:
            cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", AUDIO_BITRATE,
                    "-ar", str(AUDIO_RATE), "-ac", "2"]
        else:
            cmd += ["-map", "1:a:0", "-c:a", "aac", "-b:a", AUDIO_BITRATE,
                    "-shortest"]
        cmd += [str(self.dst)]
        return cmd


def check(master: dict, dst: dict, profile: "pp.Profile",
          expect_w: int, expect_h: int) -> tuple:
    """校验成片是否真的符合平台档案。返回 (errors, warnings)。

    这里的原则和剪映那条路一致：**产物说了算**，不信编码器的退出码。
    """
    errors, warnings = [], []
    if (dst["width"], dst["height"]) != (expect_w, expect_h):
        errors.append(f"分辨率 {dst['width']}x{dst['height']} != 期望 {expect_w}x{expect_h}")
    if abs(dst["width"] / max(1, dst["height"]) - expect_w / max(1, expect_h)) > 0.01:
        errors.append("成片比例与目标不符（疑似被拉伸）")
    if profile.fps and abs(dst["fps"] - profile.fps) > 0.05:
        errors.append(f"帧率 {dst['fps']} != 目标 {profile.fps}")
    if dst["vcodec"] not in ("h264", "hevc"):
        errors.append(f"视频编码 {dst['vcodec']} 不是 h264/hevc，多数平台不收")
    if dst["pix_fmt"] and dst["pix_fmt"] != "yuv420p":
        warnings.append(f"像素格式 {dst['pix_fmt']}，播放器兼容性差（最好 yuv420p）")
    if not dst["acodec"]:
        errors.append("没有音轨（平台普遍要求有音轨）")
    if dst["path"].rsplit(".", 1)[-1].lower() != profile.container:
        errors.append(f"容器 {dst['path'].rsplit('.', 1)[-1]} != 平台要求 {profile.container}")
    if master["duration"] and dst["duration"]:
        drift = abs(dst["duration"] - master["duration"])
        if drift > 0.5:
            errors.append(f"时长漂移 {drift:.2f}s（母版 {master['duration']:.2f}s）")
    if profile.source != pp.SOURCE_DOCUMENTED:
        warnings.append(f"平台档案来源={profile.source}（未文档核实）")
    return errors, warnings


# ------------------------------------------------------------------ 主流程
def plan_for(master: dict, profile: "pp.Profile", outdir: Path, fit: str,
             stem: str) -> Plan:
    (w, h), note = target_dims(profile, master["width"], master["height"])
    dst = outdir / f"{stem}__{profile.platform}__{w}x{h}.{profile.container}"
    return Plan(
        master=Path(master["path"]), dst=dst, profile=profile,
        width=w, height=h, fps=profile.fps, fit=fit,
        vbitrate=bitrate_bps(w, h, profile.fps, profile.bitrate_preset),
        has_audio=bool(master["acodec"]),
        notes=[note],
    )


def encode(plan: Plan, timeout: int = 3600) -> dict:
    """执行 ffmpeg 并回报真实结果。失败时把 stderr 尾巴抛出，不吞。"""
    plan.dst.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    r = subprocess.run(plan.argv(), capture_output=True, text=True, timeout=timeout)
    cost = time.time() - t0
    if r.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 失败({r.returncode}) {plan.dst.name}: {r.stderr.strip()[-500:]}")
    return {"seconds": round(cost, 1), "cmd": plan.argv()}


def deliver(master_path, platforms, outdir="~/CreatorStudio/out",
            fit: str = DEFAULT_FIT, dry_run: bool = False,
            stem: str = "", quiet: bool = False) -> dict:
    """母版 → N 个平台成片。返回证据 dict（dry_run 时不含产物）。"""
    master = probe(master_path)
    if not master["acodec"] and not quiet:
        print("  母版没有音轨 → 每个成片补一条静音轨（平台普遍要求）")
    out = Path(os.path.expanduser(str(outdir)))
    stem = stem or Path(master["path"]).stem
    if stem.endswith("__" + "master"):
        stem = stem[: -len("__master")]

    results = []
    for plat in platforms:
        profile = pp.profile_for(plat)
        plan = plan_for(master, profile, out, fit, stem)
        row = {
            "platform": profile.platform,
            "label": profile.label,
            "aspect": profile.aspect,
            "target": f"{plan.width}x{plan.height}@{profile.fps:g}",
            "container": profile.container,
            "bitrate_bps": plan.vbitrate,
            "bitrate_preset": profile.bitrate_preset,
            "fit": fit,
            "source": profile.source,
            "output": str(plan.dst),
            "notes": plan.notes,
        }
        if not quiet:
            print(f"  {profile.label:<10} {profile.aspect:>5}  "
                  f"{plan.width}x{plan.height}@{profile.fps:g}  "
                  f"{plan.vbitrate // 1000} kbps  fit={fit}")
        if dry_run:
            row["status"] = "planned"
            row["cmd"] = plan.argv()
            results.append(row)
            continue
        try:
            timing = encode(plan)
            dst = probe(plan.dst)
            errors, warnings = check(master, dst, profile, plan.width, plan.height)
            row.update({
                "status": "failed" if errors else "ok",
                "bytes": dst["size"],
                "probe": {k: dst[k] for k in
                          ("width", "height", "fps", "vcodec", "acodec",
                           "duration", "pix_fmt")},
                "errors": errors,
                "warnings": warnings,
                "render_seconds": timing["seconds"],
                "cmd": timing["cmd"] if errors else None,
            })
            if not quiet:
                mark = "✗" if errors else "✓"
                print(f"      {mark} {dst['size'] / 1048576:.2f} MB  "
                      f"{dst['duration']:.2f}s  {timing['seconds']:.1f}s 渲染")
                for e in errors:
                    print(f"      ! {e}")
        except Exception as exc:  # 单个平台失败不能带走整批
            row.update({"status": "failed", "errors": [str(exc)]})
            if not quiet:
                print(f"      ✗ {exc}")
        results.append(row)

    ok = sum(1 for r in results if r.get("status") == "ok")
    planned = sum(1 for r in results if r.get("status") == "planned")
    failed = sum(1 for r in results if r.get("status") == "failed")
    evidence = {
        "master": {k: master[k] for k in
                   ("path", "width", "height", "fps", "duration", "vcodec", "acodec")},
        "master_size": master["size"],
        "fit": fit,
        "dry_run": bool(dry_run),
        "platforms": results,
        "ok": ok,
        "planned": planned,
        "failed": failed,
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    return evidence


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="母版 → 各平台合规成片（headless，不碰 GUI）")
    ap.add_argument("--master", help="母版视频路径")
    ap.add_argument("--platform", default="", help="平台 id，逗号分隔；all=全部档案")
    ap.add_argument("--outdir", default="~/CreatorStudio/out")
    ap.add_argument("--fit", default=DEFAULT_FIT, choices=sorted(FIT_MODES),
                    help="比例不一致时的处理方式：" +
                         "；".join(f"{k}={v}" for k, v in FIT_MODES.items()))
    ap.add_argument("--stem", default="", help="输出文件名主体，默认取母版文件名")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不编码")
    ap.add_argument("--json", action="store_true", help="输出证据 JSON")
    ap.add_argument("--evidence", default="", help="证据 JSON 落盘路径")
    ap.add_argument("--list-platforms", action="store_true")
    args = ap.parse_args(argv)

    if args.list_platforms:
        for p in pp.all_profiles():
            print(f"{p.platform:<14} {p.label:<12} {p.aspect:>5}  "
                  f"{p.width}x{p.height}@{p.fps:g}  {p.container}  "
                  f"{p.bitrate_preset}  [{p.source}]")
        return 0
    if not args.master:
        ap.error("--master 必填（或用 --list-platforms）")
    if args.platform.strip().lower() == "all":
        platforms = [p.platform for p in pp.all_profiles()]
    else:
        platforms = [x.strip() for x in args.platform.split(",") if x.strip()]
    if not platforms:
        ap.error("--platform 不能为空")

    if not shutil.which(FFMPEG):
        print(f"找不到 {FFMPEG}", file=sys.stderr)
        return 3

    print(f"母版: {args.master}")
    evidence = deliver(args.master, platforms, args.outdir, args.fit,
                       args.dry_run, args.stem)
    if evidence.get("dry_run"):
        # --dry-run 只是预览，没编码当然没有"合格"的产物；按失败报是错的
        print(f"\n{evidence['planned']} 个平台已规划（dry-run 未编码），"
              f"{evidence['failed']} 个计划失败")
    else:
        print(f"\n{evidence['ok']} 个平台合格，{evidence['failed']} 个失败")

    if args.evidence:
        p = Path(os.path.expanduser(args.evidence))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(evidence, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        print(f"证据: {p}")
    if args.json:
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if evidence["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
