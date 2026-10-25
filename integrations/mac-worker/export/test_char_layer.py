"""人物动画层自检：分段 → 带 alpha 的视频，帧数/时长/动作/摆位都要对。"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import character  # noqa: E402
from character_assets import character_root  # noqa: E402
from export import char_layer  # noqa: E402

W, H, FPS = 540, 960, 12


def _probe(mov: Path) -> dict:
    raw = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v",
                          "-show_entries", "stream=nb_frames,pix_fmt,width,height",
                          "-show_entries", "format=duration", "-of", "json", str(mov)],
                         capture_output=True, text=True, check=True).stdout
    d = json.loads(raw)
    st = d["streams"][0]
    return {"frames": int(st.get("nb_frames") or 0), "pix": st["pix_fmt"],
            "w": st["width"], "h": st["height"],
            "dur": float(d["format"]["duration"])}


def _raw(mov: Path, t: float, out: Path) -> bytes:
    # webm 的 alpha 在 side data 里：不给 libvpx 解码器就只会读到不透明的画面
    dec = ["-c:v", "libvpx-vp9"] if str(mov).endswith(".webm") else []
    subprocess.run(["ffmpeg", "-v", "error", "-y", *dec, "-ss", f"{t:.3f}", "-i", str(mov),
                    "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba",
                    str(out)], check=True)
    return Path(out).read_bytes()


def main() -> int:
    root = character_root("whale")
    outfit = "01_sailor"
    outfit_root = root / "outfits" / outfit
    assert outfit_root.is_dir(), f"缺服装素材 {outfit_root}"
    has_anim = (outfit_root / "anim" / "talk").is_dir()
    segs = [
        {"start": 0.0, "end": 1.2, "width": 360, "x": 90, "y": 300,
         "pose": "idle", "mouth": "closed", "eyes": "open", "outfit": outfit,
         "anim": "idle_blink" if has_anim else "", "fade_in": 0.2,
         "_outfit_root": str(outfit_root)},
        {"start": 1.0, "end": 2.4, "width": 360, "x": 90, "y": 300,
         "pose": "wave", "mouth": "open", "eyes": "open", "outfit": outfit,
         "anim": "talk" if has_anim else "", "anim_t0": 0.3,
         "_outfit_root": str(outfit_root)},
    ]
    logs: list[dict] = []
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        built = char_layer.build(segs, width=W, height=H, fps=FPS, workdir=work,
                                 duration=2.4, render=character.render,
                                 key_green=character._key_green,
                                 log=lambda s: logs.append(json.loads(s)))
        assert built is not None, "没有生成人物层"
        mov, ox, oy = built
        assert mov.is_file(), "没有生成人物层"
        assert ox >= 0 and oy >= 0, (ox, oy)
        info = _probe(mov)
        assert info["pix"] in ("argb", "yuva420p", "yuv420p"), info
        # 只存人物实际占的框：整幅 argb 里大半是透明留白，白占几倍体积
        assert info["w"] <= W and info["h"] <= H and info["w"] > 40, info
        assert info["w"] % 2 == 0 and info["h"] % 2 == 0, info
        box = logs[-1].get("box")
        assert box == [ox, oy, info["w"], info["h"]], (box, ox, oy, info)
        assert info["w"] * info["h"] < W * H * 0.5, "裁剪没省下体积，框取大了"
        assert abs(info["dur"] - 2.4) < 0.15, info
        early = _raw(mov, 0.02, work / "a.raw")
        mid = _raw(mov, 1.9, work / "m.raw")
        late = _raw(mov, 2.36, work / "b.raw")
        # 中段不透明像素得占满人物该有的量，否则就是画到画布外了
        alpha = mid[3::4]
        opaque = sum(1 for a in alpha[::17] if a > 8)
        assert opaque > 200, f"人物没画上去：不透明采样 {opaque}"
        # 框是贴着人物裁的：必须同时有透明和不透明，全透明=alpha 没编进去
        assert 0.02 < sum(1 for a in alpha if a > 8) / len(alpha) < 0.98, "alpha 整帧一致"
        assert hashlib.md5(mid).hexdigest() != hashlib.md5(late).hexdigest(), \
            "两帧一模一样，人物没动"
        # 首帧淡入起点、末帧淡出终点都该接近全透明
        assert early[3::4][:1] and sum(early[3::4000]) < sum(mid[3::4000]), "淡入没生效"
        stats = logs[-1]
        assert stats["frames"] >= 28, stats
        # 动画帧必须真的接进来：每段只拿到 1 帧就是静态立绘 + 呼吸，
        # 出片时观众看到的是贴图，图上一切指标都还是绿的
        anims = stats.get("anims") or []
        assert len(anims) == len(segs), anims
        if has_anim:
            assert all(n > 1 for n in anims), f"有动画素材但每段只有一帧：{anims}"
        # 段内相邻帧也要动：整段静止等于人物卡住
        a = _raw(mov, 1.55, work / "s1.raw")
        b = _raw(mov, 1.62, work / "s2.raw")
        diff = sum(abs(x - y) for x, y in zip(a[::97], b[::97])) / max(1, len(a[::97]))
        assert diff > 0.4, f"同一段两帧几乎一样，人物静止：{diff:.2f}"
        # 叠化处不许把人画透明：两段各乘权重时整块人物掉到 75% 甚至 46%，
        # 成片里换手势那一瞬是灰的。交叠帧必须仍有一大片像素是实心的
        cross = _raw(mov, 1.1, work / "c.raw")[3::4]
        solid = sum(1 for x in cross if x > 8)
        strong = sum(1 for x in cross if x > 240) / max(1, solid)
        # 两边都乘权重时最高只有 191，实心像素一个都没有
        assert strong > 0.40, f"叠化帧人物发透：实心占比 {strong:.2f}"
        # 大小不许变：整条片子人物身高极差超过 3% 就是换姿势时人在放大缩小
        hs = stats.get("h") or []
        assert len(hs) == 3 and hs[0] > 0, hs
        assert hs[2] / hs[0] < 1.03, f"人物身高在片子里变了：{hs}"

    print(json.dumps({"ok": True, "anim": has_anim, **info, **stats,
                      "opaque_samples": opaque}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
