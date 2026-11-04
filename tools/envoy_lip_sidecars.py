"""给 /tmp/envoy-promo-work 的整段配音和逐拍切片生成 .words.json。

整段 a0X.mp3 是 lipsync 实际吃的那条（promo_envoy 的 lip_audio / lip_text），
a0X_pY.mp3 是按气口切开的逐拍素材。整段直接用 SCENES[*]["say"]；切片要把 say
按标点切句后分到各拍，句数对不上时按时长比例合并相邻句。

用法：python3 tools/envoy_lip_sidecars.py [a00 a01 …]
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = Path(os.environ.get(
    "CREATOR_STUDIO_MAC_WORKER",
    str(Path.home() / "CreatorStudio/runtime/integrations/mac-worker"),
))
sys.path.insert(0, str(ENGINE / "export"))
import lipsync  # noqa: E402

try:                       # lip_align 是可选的对齐器（本机不一定装）
    import lip_align  # noqa: E402
except ModuleNotFoundError:  # 没有就只用 say，够用：say 就是配音念的字
    lip_align = None

AUD = Path(os.environ.get("LIP_AUDIO_DIR", "/tmp/envoy-promo-work"))
SRC = ROOT / "deploy/presenter/promo_envoy.py"
SILENCE = "silencedetect=noise=-32dB:d=0.14"


def scenes() -> list[dict]:
    text = SRC.read_text(encoding="utf-8")
    says = re.findall(r'\bsay="([^"]*)"', text)
    names = re.findall(r'\baudio="(a\d\d\.mp3)"', text)
    if len(says) != len(names):
        raise SystemExit(f"say/audio 数量对不上: {len(says)} vs {len(names)}")
    # 配音可能是从卡片文案（points[].caption）念的，跟后来改过的 say 不同；
    # 两条都拿去对齐，谁覆盖率报得高就用谁 —— 嘴型必须跟声音走。
    caps = []
    try:
        spec = json.loads((ROOT / "deploy/specs/envoy_promo.json").read_text(encoding="utf-8"))
        caps = [str(x).split("::")[-1].strip() for x in spec["spec"]["points"]]
    except (OSError, ValueError, KeyError, TypeError):
        caps = []
    out = []
    for i, (say, name) in enumerate(zip(says, names)):
        # 第 0 段是 intro、1..4 段对应 points、最后一段是 outro
        alt = caps[i - 1] if 1 <= i <= len(caps) else None
        out.append({"say": say, "alt": alt, "audio": name})
    return out


def dur(p: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
        capture_output=True, text=True,
    ).stdout.strip()
    return float(out or 0)


def marks(p: Path) -> list[float]:
    """silencedetect 的 silence_end：每段的结束点 = 该拍的右边界。"""
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(p), "-af", SILENCE, "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return [round(float(m), 3) for m in re.findall(r"silence_end: ([0-9.]+)", proc.stderr or "")]


def _weight(s: str) -> int:
    return len(lipsync.spoke_chars(s))


def split_groups(say: str, n: int, parts: list[float]) -> list[str]:
    """把 say 分成 n 组，尽量让「字数占比」贴近「时长占比」。"""
    segs = [s for s in re.split(r"(?<=[，。；、！？])", say) if s.strip()]
    if n <= 1:
        return [say]
    if len(segs) < n:
        # 句子不够分：退化成按时长直接切字数
        text = "".join(segs)
        total = sum(parts) or 1.0
        out, cur = [], 0
        for i, d in enumerate(parts[:-1]):
            k = max(1, round(len(text) * d / total))
            out.append(text[cur:cur + k])
            cur += k
        out.append(text[cur:] or text[-1:])
        return out
    if len(segs) == n:
        return segs
    # DP：把 m 个相邻句并成 n 组
    w = [_weight(s) for s in segs]
    tot_w, tot_d = sum(w) or 1, sum(parts) or 1.0
    m = len(segs)
    from functools import lru_cache

    @lru_cache(maxsize=None)
    def go(i: int, g: int) -> tuple[float, tuple[int, ...]]:
        if g == n - 1:
            cost = (sum(w[i:]) / tot_w - parts[g] / tot_d) ** 2
            return cost, ()
        bestv = (1e9, ())
        for cut in range(i + 1, m - (n - g - 1) + 1):
            cost = (sum(w[i:cut]) / tot_w - parts[g] / tot_d) ** 2
            rest, cuts = go(cut, g + 1)
            if cost + rest < bestv[0]:
                bestv = (cost + rest, (cut,) + cuts)
        return bestv

    _, cuts = go(0, 0)
    out, prev = [], 0
    for c in list(cuts) + [m]:
        out.append("".join(segs[prev:c]))
        prev = c
    return out


def choose(say: str, alt: str | None, audio: Path) -> tuple[str, str]:
    """say 与卡片文案各对齐一次，取覆盖率高的那条；返回 (文本, 来源标签)。"""
    if lip_align is None or not alt or alt == say:
        return say, "say"
    a = lip_align.align(say, audio)
    b = lip_align.align(alt, audio)
    ca, cb = float(a.get("coverage") or 0), float(b.get("coverage") or 0)
    if cb > ca + 0.02:
        print("  ! %s 配音念的是卡片文案 (cov %.2f → %.2f)" % (audio.name, ca, cb), flush=True)
        return alt, "caption"
    return say, "say"


def describe(p: Path, blob: dict) -> str:
    v = lip_align.verify(blob["text"], p)
    return ("%-16s %5.2fs chars=%-3d cov=%.2f err=%.3fs corr=%+.3f lead=%+dms "
            "sil=%d src=%s" % (
                p.name, dur(p), len(blob.get("chars") or []), blob["coverage"],
                blob["mean_err"], v["corr"], v["lead_ms"], v["silence_open"], v["source"]))


def do(text: str, audio: Path) -> dict | None:
    if lip_align is None:
        return None
    side = lip_align.ensure(text, audio)
    if side is None:
        print("%-16s FAILED" % audio.name, flush=True)
        return None
    blob = json.loads(side.read_text(encoding="utf-8"))
    print(describe(audio, blob), flush=True)
    return blob


def main(argv: list[str]) -> int:
    pick = {a if a.endswith(".mp3") else a + ".mp3" for a in argv[1:]}
    rows = []
    for sc in scenes():
        name = sc["audio"]
        audio = AUD / name
        stem = audio.stem
        beats = [p for p in sorted(AUD.glob(f"{stem}_p*.mp3")) if p.name != f"{stem}_p1_slow.mp3"]
        if not beats:  # 配音脚本按气口切出来的 _b*_raw.mp3
            beats = sorted(AUD.glob(f"{stem}_b*_raw.mp3"))
        slow = AUD / f"{stem}_p1_slow.mp3"
        if pick and not any(x == name or x.startswith(stem + "_p")
                            or x.startswith(stem + "_b") for x in pick):
            continue
        # 用整段配音实际念的文本对齐：lipsync 吃的就是这条
        want = (not pick) or name in pick or any(
            x.startswith(stem + "_p") or x.startswith(stem + "_b") for x in pick)
        text_src = sc["say"]
        if want:
            text_src, _label = choose(sc["say"], sc.get("alt"), audio)
        if not pick or name in pick:
            rows.append(("scene", name, do(text_src, audio)))
        groups = split_groups(text_src, len(beats), [dur(p) for p in beats])
        for part, text in zip(beats, groups):
            if pick and part.name not in pick:
                continue
            print("  %-14s ← %s" % (part.name, text), flush=True)
            rows.append(("part", part.name, do(text, part)))
        if slow.exists() and (not pick or slow.name in pick):
            text = groups[1] if len(groups) > 1 else sc["say"]
            print("  %-14s ← %s (slow)" % (slow.name, text), flush=True)
            rows.append(("part", slow.name, do(text, slow)))
    if lip_align is None:
        print("缺 lip_align：本机没装这个对齐器。配音 sidecar 由 tools/promo/tts_envoy.py "
              "直接写（edge-tts WordBoundary 更准），这个工具只在需要重对齐旧音频时用。")
        return 2
    ok = [r for r in rows if r[2]]
    print("\n== %d/%d sidecars" % (len(ok), len(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
