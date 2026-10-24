#!/usr/bin/env python3
"""timeline_render.py 的自测。直接跑：python3 test_timeline_render.py

用**纯色**画面做夹具（白段 + 黑段），这样所有断言都能靠亮度采样硬判：
切点是否真的换了、字幕是否在窗口内出现、窗口外是否消失。
不读图片，不需要人看。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import timeline_render as T  # noqa: E402

HAS_FFMPEG = bool(shutil.which(T.FFMPEG))


def mean_luma(path, at=0.0, crop=None) -> float:
    vf = f"crop={crop}" if crop else "null"
    r = subprocess.run(
        [T.FFMPEG, "-v", "error", "-ss", f"{at:g}", "-i", str(path),
         "-vf", vf, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, timeout=120)
    if r.returncode != 0 or not r.stdout:
        raise AssertionError(f"采样失败: {r.stderr.decode()[-300:]}")
    return sum(r.stdout) / len(r.stdout)


class TestColorParse(unittest.TestCase):
    def test_hex_and_alpha(self):
        self.assertEqual(T._color("#112233", "#000000"), (0x11, 0x22, 0x33, 255))
        self.assertEqual(T._color("#11223344", "#000000"), (0x11, 0x22, 0x33, 0x44))
        self.assertEqual(T._color(None, "#ffffff"), (255, 255, 255, 255))

    def test_bad_hex(self):
        with self.assertRaises(T.SpecError):
            T._color("#12345", "#000000")


class TestSpecValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tl-spec-"))
        self.clip = self.tmp / "c.mp4"
        if HAS_FFMPEG:
            subprocess.run(
                [T.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "color=c=white:s=320x180:d=1:r=30",
                 "-c:v", "libx264", "-preset", "ultrafast",
                 "-pix_fmt", "yuv420p", str(self.clip)], check=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, obj):
        p = self.tmp / "spec.json"
        p.write_text(json.dumps(obj), encoding="utf-8")
        return p

    def _ok(self, **over):
        obj = {"canvas": {"width": 320, "height": 180, "fps": 30},
               "video": [{"src": "c.mp4", "in": 0, "out": 1}]}
        obj.update(over)
        return obj

    def test_minimal_ok(self):
        spec = T.load_spec(self._write(self._ok()))
        self.assertEqual(spec["duration"], 1.0)
        self.assertEqual(spec["text"], [])
        self.assertEqual(spec["canvas"]["width"], 320)

    def test_missing_out_rejected(self):
        with self.assertRaises(T.SpecError) as cm:
            T.load_spec(self._write(self._ok(
                video=[{"src": "c.mp4", "in": 0}])))
        self.assertIn("out", str(cm.exception))

    def test_missing_src_rejected(self):
        with self.assertRaises(T.SpecError):
            T.load_spec(self._write(self._ok(
                video=[{"src": "nope.mp4", "in": 0, "out": 1}])))

    def test_odd_canvas_rejected(self):
        with self.assertRaises(T.SpecError):
            T.load_spec(self._write(self._ok(
                canvas={"width": 321, "height": 181, "fps": 30})))

    def test_bad_fit_rejected(self):
        with self.assertRaises(T.SpecError):
            T.load_spec(self._write(self._ok(
                video=[{"src": "c.mp4", "in": 0, "out": 1, "fit": "stretch"}])))

    def test_text_after_end_rejected(self):
        with self.assertRaises(T.SpecError):
            T.load_spec(self._write(self._ok(
                text=[{"text": "x", "start": 5, "end": 6}])))

    def test_speed_shrinks_duration(self):
        spec = T.load_spec(self._write(self._ok(
            video=[{"src": "c.mp4", "in": 0, "out": 1, "speed": 2}])))
        self.assertAlmostEqual(spec["duration"], 0.5, places=3)


class TestTextPng(unittest.TestCase):
    def setUp(self):
        try:
            self.font = T.find_font()
        except T.SpecError as exc:
            self.skipTest(str(exc))

    def test_renders_transparent_png_with_ink(self):
        from PIL import Image
        tmp = Path(tempfile.mkdtemp(prefix="tl-png-"))
        try:
            item = {"text": "第一行\n第二行", "size": 0.1, "color": (255, 255, 255, 255),
                    "stroke": 2, "stroke_color": (0, 0, 0, 255),
                    "box": (0, 0, 0, 200), "radius": 0.5, "align": "center"}
            p, w, h = T.render_text_png(item, {"width": 1920, "height": 1080},
                                        self.font, tmp / "t.png")
            self.assertGreater(w, 40)
            self.assertGreater(h, 40)
            img = Image.open(p).convert("RGBA")
            alpha = img.getchannel("A")
            self.assertIsNotNone(alpha.getbbox(), "整张图全透明 = 没画出字")
            self.assertEqual(img.getpixel((0, 0))[3], 0, "四角应透明（圆角底衬）")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_position_kept_inside_canvas(self):
        canvas = {"width": 1920, "height": 1080}
        x, y = T._text_position({"x": 0.0, "y": 0.0}, canvas, 400, 100)
        self.assertEqual((x, y), (0, 0), "越界应夹回画面内")
        x, y = T._text_position({"x": 1.0, "y": 1.0}, canvas, 400, 100)
        self.assertEqual((x, y), (1520, 980))


@unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
class TestRender(unittest.TestCase):
    """白段 + 黑段 + 一条字幕 + 一条 BGM，全部靠亮度断言。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="tl-render-"))
        asset = cls.tmp / "asset"
        asset.mkdir()

        def make(name, color, dur="2"):
            subprocess.run(
                [T.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i",
                 f"color=c={color}:s=320x180:d={dur}:r=30",
                 "-c:v", "libx264", "-preset", "ultrafast",
                 "-pix_fmt", "yuv420p", str(asset / name)], check=True)

        make("white.mp4", "white")
        make("black.mp4", "black")
        subprocess.run(
            [T.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
             "-c:a", "aac", str(asset / "bgm.m4a")], check=True)

        cls.spec_obj = {
            "canvas": {"width": 320, "height": 180, "fps": 30},
            "video": [
                {"src": "asset/white.mp4", "in": 0, "out": 2, "fit": "cover"},
                {"src": "asset/black.mp4", "in": 0, "out": 2, "fit": "contain"},
            ],
            "text": [{
                "text": "字幕", "start": 0.5, "end": 1.5,
                "x": 0.5, "y": 0.5, "size": 0.25,
                "color": "#000000", "stroke": 0, "box": "#00000000",
            }],
            "audio": [{"src": "asset/bgm.m4a", "start": 0, "volume": 0.5,
                       "loop": True}],
        }
        sp = cls.tmp / "spec.json"
        sp.write_text(json.dumps(cls.spec_obj), encoding="utf-8")
        cls.spec = T.load_spec(sp)
        cls.ev = T.render(cls.spec, cls.tmp / "master.mp4")
        cls.master = cls.tmp / "master.mp4"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_container_and_duration(self):
        import deliver as D
        p = D.probe(self.master)
        self.assertEqual((p["width"], p["height"]), (320, 180))
        self.assertAlmostEqual(p["fps"], 30.0, places=2)
        self.assertAlmostEqual(p["duration"], 4.0, delta=0.2)
        self.assertEqual(p["vcodec"], "h264")
        self.assertEqual(p["acodec"], "aac", "平台要求必须有音轨")

    def test_exact_frame_count(self):
        r = subprocess.run(
            [T.FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-count_frames", "-show_entries", "stream=nb_read_frames",
             "-of", "csv=p=0", str(self.master)],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(r.stdout.strip(), "120", "4s@30 必须正好 120 帧")

    def test_clip_boundary_really_cuts(self):
        a = mean_luma(self.master, 1.0)   # 白段
        b = mean_luma(self.master, 1.9)   # 仍白段
        c = mean_luma(self.master, 2.1)   # 已黑段
        self.assertGreater(a, 180, f"白段应亮，实测 {a:.0f}")
        self.assertGreater(b, 180)
        self.assertLess(c, 40, f"黑段应暗，实测 {c:.0f}（切点没生效？）")

    def test_subtitle_appears_only_inside_window(self):
        # 字幕是黑字画在白色画面上 → 中带亮度必须下降
        inside = mean_luma(self.master, 1.0, crop="120:40:100:70")
        before = mean_luma(self.master, 0.2, crop="120:40:100:70")
        after = mean_luma(self.master, 1.8, crop="120:40:100:70")
        self.assertGreater(before, 180, f"窗口前应无字幕，实测 {before:.0f}")
        self.assertLess(inside, before - 30,
                        f"窗口内字幕没出现（{before:.0f}→{inside:.0f}）")
        self.assertGreater(after, 180, f"窗口后字幕没消失，实测 {after:.0f}")

    def test_audio_loudness_normalised(self):
        r = subprocess.run(
            [T.FFMPEG, "-hide_banner", "-i", str(self.master),
             "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180)
        text = r.stderr
        val = None
        for ln in text.splitlines():
            if "I:" in ln and "LUFS" in ln:
                try:
                    val = float(ln.split("I:")[1].split("LUFS")[0])
                except (IndexError, ValueError):
                    pass
                break
        self.assertIsNotNone(val, "ebur128 没输出整体响度")
        self.assertLess(abs(val + 16), 4, f"响度 {val:.1f} LUFS 偏离 -16 目标过多")

    def test_silent_spec_still_gets_audio_track(self):
        obj = dict(self.spec_obj)
        obj = json.loads(json.dumps(obj))
        obj["audio"] = []
        sp = self.tmp / "silent.json"
        sp.write_text(json.dumps(obj), encoding="utf-8")
        spec = T.load_spec(sp)
        ev = T.render(spec, self.tmp / "silent.mp4")
        import deliver as D
        p = D.probe(ev["output"])
        self.assertEqual(p["acodec"], "aac")
        self.assertAlmostEqual(p["duration"], 4.0, delta=0.2)

    def test_pipeline_into_platform_delivery(self):
        """母版 → deliver 出竖版，端到端一条链跑通（这就是 C 路线的主干）。"""
        import deliver as D
        ev = D.deliver(self.master, ["douyin"], self.tmp / "out",
                       fit="blur", quiet=True)
        self.assertEqual(ev["failed"], 0, ev["platforms"][0].get("errors"))
        out = D.probe(ev["platforms"][0]["output"])
        # 母版只有 320x180，deliver 不允许放大，所以目标是按短边等比缩下来的
        (ew, eh), _ = D.target_dims(D.pp.profile_for("douyin"), 320, 180)
        self.assertEqual((out["width"], out["height"]), (ew, eh))
        self.assertEqual(out["width"] * 16, out["height"] * 9, "竖版 9:16")
        # 取 1.0s（母版是白段）：模糊背景应是亮灰，不是纯黑
        top = mean_luma(ev["platforms"][0]["output"], 1.0,
                        crop=f"{ew}:{eh // 4}:0:0")
        self.assertGreater(top, 40, "竖版顶部应是模糊内容而不是纯黑")


if __name__ == "__main__":
    unittest.main(verbosity=2)
