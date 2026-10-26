"""paper_short.py — 纸面版竖屏短片：同一份脚本，画面层换成整页纸面。

factcard 是「一屏一张卡」，投到竖屏里还是 PPT 的骨架。纸面版把整条片当成
一张不断往上走的纸：同一页上留标题、正文、脚注，靠版式和时间错位推进，
画面不换页，所以没有「翻页感」。

走法：先用 vertical.build_spec 把脚本变成时间轴（配音、卡面、人物层都在
那儿算好），这里只丢掉卡面、拿它的时间点和音轨，纸面自己一格一格画。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import paper_render_engine as PAPER
import timeline_render as TR
import vertical_short as VERTICAL

FPS = 30
XFADE = 0.40

KICKER = "ENVOY / 简报"


def _rows(script: dict[str, Any], bounds: list[tuple[float, float]]) -> list[dict[str, Any]]:
    """脚本 → 纸面行：主标题、正文、脚注各自有位置，别一屏四个字号乱堆。"""
    nar = script.get("narration") or {}
    points = list(script.get("points") or [])
    lines = list(nar.get("points") or [])
    rows: list[dict[str, Any]] = [{
        "kicker": KICKER,
        "headline": str(script.get("hook") or ""),
        "body": str(nar.get("intro") or ""),
        "foot": str(script.get("topic") or ""),
    }]
    for i, p in enumerate(points):
        parts = [x.strip() for x in str(p).split("::")]
        rows.append({
            "kicker": f"{i + 1:02d} / {len(points):02d}",
            "headline": parts[0] if parts else "",
            "body": lines[i] if i < len(lines) else "",
            "foot": parts[1] if len(parts) > 1 else "",
        })
    cta = str(script.get("cta") or "")
    tail = str(nar.get("outro") or "")
    # 台词里重复的那半句留着，纸面上只写一遍
    tail = re.sub(r"^" + re.escape(cta) + r"[。，,.\s]*", "", tail)
    rows.append({
        "kicker": "收尾",
        "headline": cta,
        "body": tail,
        "foot": str(script.get("kicker") or ""),
    })
    return rows[: len(bounds)] if len(rows) > len(bounds) else rows


def _bounds(pacing: dict[str, Any], total: float) -> list[tuple[float, float]]:
    """pacing 只给每屏时长，起点自己累加；末屏吃掉片尾淡出那点余量。"""
    out: list[tuple[float, float]] = []
    t = 0.0
    for s in pacing.get("scenes") or []:
        slot = float(s.get("slot") or 0.0)
        if slot <= 0:
            continue
        out.append((t, t + slot))
        t += slot
    if out and total > out[-1][1]:
        out[-1] = (out[-1][0], round(total, 3))
    return out


def _audio_chain(spec: dict[str, Any], total: float, first: int) -> tuple[list[str], list[str], str]:
    """把 spec.audio 拼成一条音轨：延时对齐、淡入淡出、最后整体响度归一。"""
    parts: list[str] = []
    inputs: list[str] = []
    for k, a in enumerate(spec["audio"]):
        inputs += ["-i", str(a["src"])]
        fade = float(a.get("fade") or 0.0)
        chain = (f"[{first + k}:a]aresample=48000,aformat=sample_fmts=fltp:"
                 f"channel_layouts=stereo,volume={float(a.get('volume') or 1.0):g}")
        if fade > 0:
            chain += f",afade=t=in:st=0:d={fade:g}"
            tail = max(0.0, total - float(a["start"]) - fade)
            chain += f",afade=t=out:st={tail:g}:d={fade:g}"
        delay = int(round(float(a["start"]) * 1000))
        if delay:
            chain += f",adelay={delay}|{delay}"
        chain += f",apad=whole_dur={total:g}[a{k}]"
        parts.append(chain)
    mix = "".join(f"[a{k}]" for k in range(len(spec["audio"])))
    parts.append(f"{mix}amix=inputs={len(spec['audio'])}:duration=longest:normalize=0,"
                 f"loudnorm=I=-16:TP=-1.5:LRA=11,atrim=0:{total:g}[aout]")
    return parts, inputs, "[aout]"


def build(script: dict[str, Any], workdir: Path, *, voice: str = "",
          logo: Path | None = None) -> dict[str, Any]:
    """脚本文本 → 时间轴 + 纸面行。返回的东西够 render 直接出货。"""
    built = VERTICAL.build_spec(script, workdir, voice=voice, logo=logo)
    meta = built.get("meta") or {}
    bounds = _bounds(meta.get("pacing") or {}, float(meta.get("duration") or 0.0))
    if not bounds:
        raise RuntimeError("时间轴里没有 pacing，无法排纸面")
    rows = _rows(script, bounds)
    if len(rows) < len(bounds):
        # 脚本比场景短：多出来的场景沿用最后一行，宁可重复也不留空页
        rows += [rows[-1]] * (len(bounds) - len(rows))
    plan = []
    for i, (st, en) in enumerate(bounds):
        row = dict(rows[i])
        row.update({"start": round(st, 3), "end": round(en, 3)})
        plan.append(row)
    return {"spec": built, "plan": plan, "total": bounds[-1][1]}


def render(script: dict[str, Any], workdir: Path, out_mp4: Path, *,
           voice: str = "", logo: Path | None = None, style: str = "paper",
           shots: list[tuple[str, str]] | None = None, log=print) -> dict[str, Any]:
    """纸面帧 + 人物层 + 音轨 → 母版。返回出货信息（时长/场景数/人物层）。"""
    workdir.mkdir(parents=True, exist_ok=True)
    built = build(script, workdir, voice=voice, logo=logo)
    spec, plan, total = built["spec"], built["plan"], built["total"]

    spec_path = workdir / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    if shots:
        # shots: [(图片路径, 角标文案)]，按屏序贴上去；真截图比画出来的图形可信
        for row, pair in zip(plan, shots):
            row["shot"], row["cap"] = str(pair[0]), str(pair[1])

    silent = workdir / "paper_silent.mp4"
    log(f"[paper] {len(plan)} 屏 {total:.2f}s style={style}")
    PAPER.render(plan, silent, audio=None, workdir=workdir / "paper", style=style, log=log)

    char = next((o for o in (spec.get("overlay") or [])
                 if str(o.get("src", "")).endswith("char_layer.webm")), None)
    cmd = [TR.FFMPEG, "-v", "error", "-y",
           "-i", str(silent)]
    first_audio = 1
    filt: list[str] = ["[0:v]null[vbase]"]
    last = "[vbase]"
    if char is not None:
        cmd += ["-c:v", "libvpx-vp9", "-i", str(char["src"])]
        first_audio = 2
        cx = float(char.get("x") or 0.0)
        cy = float(char.get("y") or 0.0)
        st = float(char.get("start") or 0.0)
        en = float(char.get("end") or total)
        filt.append(f"[1:v]format=rgba,trim=start={st:.3f}:end={en:.3f},"
                    f"setpts=PTS-STARTPTS[char]")
        filt.append(f"{last}[char]overlay=x={cx:g}:y={cy:g}:eof_action=pass:"
                    f"enable='between(t,{st:.3f},{en:.3f})'[vout]")
        last = "[vout]"
    else:
        filt.append("[vbase]null[vout]")
        last = "[vout]"
    parts, a_inputs, aout = _audio_chain(spec, total, first_audio)
    filt += parts
    cmd += a_inputs + ["-filter_complex", ";".join(filt), "-map", last, "-map", aout,
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
                       "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", f"{FPS:g}",
                       "-t", f"{total:g}", "-c:a", "aac", "-b:a", "192k",
                       "-ar", "48000", "-ac", "2", "-movflags", "+faststart",
                       str(out_mp4)]
    log("[paper] 合成人物层 + 音轨")
    subprocess.run(cmd, check=True)
    if not out_mp4.exists():
        raise RuntimeError("纸面母版没有生成")
    return {"engine": style if style != "paper" else "paper", "videoPath": str(out_mp4), "duration": round(total, 3),
            "scenes": len(plan), "character": char is not None,
            "video": str(silent), "specPath": str(spec_path)}
