#!/usr/bin/env python3
"""口型轨自检：符号是否齐全 + 能不能真的产出一条轨。

为什么需要它：`_word_window` 被误删过一次，`build()` 里的 try/except 把
NameError 吞掉，成片照出、口型轨整个丢失——成片级质检只看结果，指不出是哪
一步没的。这个检查 2 秒内把「名字没了」变成显式红字。

用法：
    python tools/check_char_layer.py                    # 只查符号
    python tools/check_char_layer.py a00.mp3 你好世界     # 真跑一条轨
"""
from __future__ import annotations

import sys
from pathlib import Path

REQUIRED = [
    "_word_window", "_enforce_quiet", "_relax_stop_gaps", "_silence_run_frames",
    "_frame_db", "_mouth_states", "_envelope", "_clamp_lip_to_audio",
    "build", "hand_shift", "plan_transitions",
]


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from export import char_layer as CL

    missing = [n for n in REQUIRED if not hasattr(CL, n)]
    if missing:
        print(f"缺符号：{', '.join(missing)}（口型轨会静默降级，先补回来）")
        return 1
    print(f"符号齐全：{len(REQUIRED)} 个")

    if len(argv) > 1:
        mp3 = Path(argv[1])
        if not mp3.exists():
            print(f"没有这个音频：{mp3}")
            return 2
        text = argv[2] if len(argv) > 2 else "你好世界，这是一次口型自检。"
        dur = float(argv[3]) if len(argv) > 3 else None
        if dur is None:
            import subprocess
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(mp3)],
                capture_output=True, text=True, check=True).stdout.strip()
            dur = float(out)
        states = CL.build(text, str(mp3), dur, fps=30.0)
        if not states:
            print("build 返回空轨")
            return 3
        from collections import Counter
        top = Counter(states).most_common(4)
        print(f"轨 {len(states)} 格 · 期望 {round(dur * 30)} · 分布 {top}")
        if abs(len(states) - round(dur * 30)) > 2:
            print("长度对不上：轨长必须等于 dur*fps，否则画到时间轴上会整体漂移")
            return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
