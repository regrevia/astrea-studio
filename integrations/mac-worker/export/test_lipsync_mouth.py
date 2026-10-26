#!/usr/bin/env python3
"""逐字口型成片自检：真配音 → 口型轨 → 1080x1920 嘴部特写 mp4 + 客观指标。

跑法（在 mac-worker 下）：
    STUDIO_SKIP_PREFLIGHT=1 /usr/bin/python3 export/test_lipsync_mouth.py

产出（~/CreatorStudio/materials/lipsync/）：
    mouth_closeup_1080x1920.mp4   三句真配音，嘴部放大特写，逐帧标状态
    lipsync_mouth_report.json     帧数/漂移/静音误开/切档率/各档占比

指标口径：
    帧数      len(states) 必须等于 round(dur*50)，±2 帧内
    漂移      口型开合曲线与配音包络做互相关，整段 lag 报告毫秒数；
              首/中/末三段分别再测一次，三者接近=常数偏移（lipsync 内部
              20ms 前瞻 + 90ms 驻留），随时间变大=累积漂移（缺陷）
    静音误开  包络 <2% 峰的帧里非 closed 的比例，越低越好
    切档率    每秒状态切换次数，目标 <=6
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import character  # noqa: E402
import mouth_art  # noqa: E402
import export.char_layer as CL  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

HZ = 50.0
W, H = 1080, 1920
OUT_DIR = Path.home() / "CreatorStudio" / "materials" / "lipsync"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"

LINES = [
    "今天这条短片，我们先说结论，再说怎么做到的。",
    "画面上的每一句话，都会跟着配音换口型。",
    "结尾留三秒，让观众把这句话看完再走。",
]


def font(sz: int):
    for f in ("/System/Library/Fonts/Supplemental/Arial.ttf",
              "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(f, sz)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def say_mp3(text: str, dst: Path) -> Path:
    """用系统 TTS 合成真配音（不用静音/合成包络，否则测不出漂移）。"""
    aiff = dst.with_suffix(".aiff")
    subprocess.run(["/usr/bin/say", "-v", "Tingting", "-o", str(aiff), text],
                   check=True)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(aiff),
                    "-ac", "1", "-ar", "44100", "-codec:a", "libmp3lame",
                    "-q:a", "4", str(dst)], check=True)
    aiff.unlink(missing_ok=True)
    return dst


def duration_of(path: Path) -> float:
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                          "format=duration", "-of",
                          "default=nw=1:nk=1", str(path)],
                         check=True, capture_output=True, text=True).stdout
    return float(out.strip())


def pcm(path: Path) -> "list[float]":
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path),
                          "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                         check=True, capture_output=True).stdout
    import array
    a = array.array("h")
    a.frombytes(raw)
    return [v / 32768.0 for v in a]


def env_at_hz(samples: "list[float]", hz: float = HZ,
              win_ms: float = 20.0) -> "list[float]":
    """每 hop 一个 RMS，hop = 1/hz；一步到底，不插值不滤波。"""
    step = 16000.0 / hz
    win = max(1, int(16000.0 * win_ms / 1000.0))
    out = []
    n = len(samples)
    i = 0.0
    while i < n:
        a = int(i)
        seg = samples[a:a + win]
        out.append(math.sqrt(sum(v * v for v in seg) / len(seg)) if seg else 0.0)
        i += step
    return out


def norm(v: "list[float]") -> "list[float]":
    m = max(v) if v else 0.0
    return [x / m for x in v] if m > 0 else v


def corr(a: "list[float]", b: "list[float]") -> float:
    n = min(len(a), len(b))
    if n < 8:
        return 0.0
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    va = math.sqrt(sum((x - ma) ** 2 for x in a))
    vb = math.sqrt(sum((x - mb) ** 2 for x in b))
    if va == 0 or vb == 0:
        return 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va * vb)


def lag_ms(track: "list[float]", env: "list[float]", span: int = 60) -> "tuple[float, float]":
    """track 相对 env 平移多少帧时相关最高（正=track 晚）。返回 (lag_ms, r)。"""
    best, barg = 0.0, -2.0
    for s in range(-span, span + 1):
        if s >= 0:
            a = track[s:]
            b = env[:len(a)]
        else:
            b = env[-s:]
            a = track[:len(b)]
        r = corr(a, b)
        if r > barg:
            barg, best = r, s
    return best * (1000.0 / HZ), barg


RANK = {"closed": 0, "teeth": 1, "pucker": 1, "narrow": 2,
        "half": 3, "mid": 3, "wide": 4, "open": 4, "round": 4}


def measure(mp3: Path, states: "list[str]", dur: float) -> dict:
    samples = pcm(mp3)
    env = norm(env_at_hz(samples))
    n = min(len(env), len(states))
    env, states = env[:n], states[:n]
    open_track = [RANK.get(s, 0) / 4.0 for s in states]
    lag, r = lag_ms(open_track, env)
    thirds = []
    for k in range(3):
        a, b = k * n // 3, (k + 1) * n // 3
        lg, rr = lag_ms(open_track[a:b], env[a:b], span=25)
        thirds.append({"lag_ms": round(lg, 1), "r": round(rr, 3)})
    quiet = [i for i, e in enumerate(env) if e < 0.02]
    false_open = sum(1 for i in quiet if states[i] != "closed")
    switches = sum(1 for i in range(1, n) if states[i] != states[i - 1])
    share = {}
    for s in states:
        share[s] = share.get(s, 0) + 1
    share = {k: round(v / max(1, n) * 100, 1) for k, v in sorted(
        share.items(), key=lambda kv: -kv[1])}
    return {
        "audio": mp3.name,
        "dur_s": round(dur, 3),
        "frames_expect": int(round(dur * HZ)),
        "frames_actual": len(states),
        "frame_delta": len(states) - int(round(dur * HZ)),
        "lag_ms": round(lag, 1),
        "r": round(r, 3),
        "thirds": thirds,
        "quiet_frames": len(quiet),
        "silence_false_open_pct": round(false_open / max(1, len(quiet)) * 100, 1)
        if quiet else 0.0,
        "switches": switches,
        "switches_per_s": round(switches / max(1e-6, dur), 2),
        "state_share_pct": share,
    }


def render_closeup(segs: "list[dict]", dst: Path) -> Path:
    """把口型轨按 50fps 铺成 1080x1920 嘴部特写：素材嘴部区域放大 4 倍。"""
    sprites: "dict[str, Image.Image]" = {}
    box = None
    for st in sorted({s for seg in segs for s in seg["states"]}):
        im = character.render(pose="point", mouth=st, eyes="open",
                              outfit="01_sailor").convert("RGB")
        sprites[st] = im
    found = mouth_art._locate(character.render(
        pose="point", mouth="open", eyes="open", outfit="01_sailor").convert("RGBA"))
    if found:
        box = found[0]
    else:  # 兜底：按经验位置裁
        box = (int(1024 * 0.55), int(1024 * 0.53),
               int(1024 * 0.61), int(1024 * 0.57))
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    cw, ch = 200, 150
    crop = (cx - cw // 2, cy - ch // 2, cx + cw // 2, cy + ch // 2)
    scale = 4
    tw, th = cw * scale, ch * scale
    f_lab = font(44)
    f_num = font(30)
    frames_dir = dst.parent / "_frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)

    idx = 0
    for seg in segs:
        off = seg.get("t0", 0.0)
        for k, st in enumerate(seg["states"]):
            im = sprites[st].crop(crop).resize((tw, th), Image.LANCZOS)
            canvas = Image.new("RGB", (W, H), (26, 26, 30))
            canvas.paste(im, ((W - tw) // 2, (H - th) // 2 - 80))
            d = ImageDraw.Draw(canvas)
            d.text(((W - tw) // 2, (H - th) // 2 - 140), st, fill=(240, 240, 245),
                   font=f_lab)
            d.text((40, H - 130), seg["text"], fill=(210, 214, 222), font=f_num)
            d.text((40, H - 80), f"{off + k / HZ:6.2f}s  frame {idx}",
                   fill=(150, 156, 168), font=f_num)
            path = frames_dir / f"{idx:05d}.png"
            canvas.save(path)
            idx += 1
    audio_all = dst.parent / "_closeup_audio.mp3"
    concat = []
    for i, seg in enumerate(segs):
        concat.extend([seg["mp3"]] * 0)  # 保持可读性
    # 按顺序拼音频
    lst = dst.parent / "_concat.txt"
    with open(lst, "w") as fh:
        for seg in segs:
            fh.write(f"file '{seg['mp3']}'\n")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-ac", "1", "-ar", "44100",
                    "-codec:a", "libmp3lame", "-q:a", "4", str(audio_all)],
                   check=True)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-framerate", str(HZ),
                    "-i", str(frames_dir / "%05d.png"), "-i", str(audio_all),
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                    "-shortest", str(dst)], check=True)
    return dst


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="lipsync_mouth_"))
    CL._LIP_TRACK_CACHE.clear()
    segs, metrics = [], []
    t0 = 0.0
    for i, text in enumerate(LINES):
        mp3 = say_mp3(text, work / f"l{i}.mp3")
        dur = duration_of(mp3)
        states = CL._mouth_states_text(str(mp3), text, dur)
        m = measure(mp3, states, dur)
        metrics.append(m)
        print(json.dumps(m, ensure_ascii=False))
        segs.append({"text": text, "mp3": str(mp3), "states": states, "t0": t0})
        t0 += len(states) / HZ
    dst = OUT_DIR / "mouth_closeup_1080x1920.mp4"
    render_closeup(segs, dst)
    report = {"clip": str(dst), "hz": HZ, "lines": metrics}
    (OUT_DIR / "lipsync_mouth_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2))
    print("clip", dst)
    for m in metrics:
        print(f"  {m['frames_actual']}/{m['frames_expect']} 帧 差 {m['frame_delta']:+d}"
              f"  lag {m['lag_ms']:+.0f}ms r {m['r']:+.3f}"
              f"  静音误开 {m['silence_false_open_pct']}%"
              f"  切档 {m['switches_per_s']}/s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
