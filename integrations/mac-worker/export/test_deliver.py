#!/usr/bin/env python3
"""deliver.py 的自测。直接跑：python3 test_deliver.py

这台机器上 pytest 不可用（缺依赖），所以用标准库 unittest。
纯逻辑测试不需要 ffmpeg；集成测试会自动用 ffmpeg 生成一段真实测试信号，
**不读任何图片进模型**，全部靠像素亮度采样做几何断言。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deliver as D  # noqa: E402
import platform_profiles as pp  # noqa: E402

HAS_FFMPEG = bool(shutil.which(D.FFMPEG) and shutil.which(D.FFPROBE))


def luma(path, w, h, x, y, at=0.3) -> float:
    """取某一矩形区域的平均亮度（0-255，limited range 黑≈16）。"""
    r = subprocess.run(
        [D.FFMPEG, "-v", "error", "-ss", f"{at:g}", "-i", str(path),
         "-vf", f"crop={w}:{h}:{x}:{y}", "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, timeout=120,
    )
    if r.returncode != 0 or not r.stdout:
        raise AssertionError(f"采样失败: {r.stderr.decode()[-300:]}")
    return sum(r.stdout) / len(r.stdout)


# ---------------------------------------------------------------- 纯逻辑
class TestTargetDims(unittest.TestCase):
    def test_same_aspect_exact(self):
        (w, h), note = D.target_dims(pp.profile_for("bilibili"), 1920, 1080)
        self.assertEqual((w, h), (1920, 1080))
        self.assertIn("1920x1080", note)

    def test_never_upscale(self):
        # 720p 母版投 B 站：降到母版能给的最大尺寸，不放大
        (w, h), note = D.target_dims(pp.profile_for("bilibili"), 1280, 720)
        self.assertEqual((w, h), (1280, 720))
        self.assertIn("不放大", note)

    def test_upscale_guard_is_even(self):
        (w, h), _ = D.target_dims(pp.profile_for("bilibili"), 1279, 719)
        self.assertEqual((w % 2, h % 2), (0, 0), "h264 要求偶数边长")

    def test_portrait_from_landscape_master(self):
        (w, h), _ = D.target_dims(pp.profile_for("douyin"), 1920, 1080)
        self.assertEqual((w, h), (1080, 1920), "目标由档案决定，与母版方向无关")

    def test_4k_master_still_delivers_1080(self):
        (w, h), _ = D.target_dims(pp.profile_for("douyin"), 3840, 2160)
        self.assertEqual((w, h), (1080, 1920), "大母版不为平台档位放大")


class TestBitrate(unittest.TestCase):
    def test_preset_ordering(self):
        a = D.bitrate_bps(1920, 1080, 30, "更低")
        b = D.bitrate_bps(1920, 1080, 30, "推荐")
        c = D.bitrate_bps(1920, 1080, 30, "更高")
        self.assertLess(a, b)
        self.assertLess(b, c)

    def test_reasonable_range(self):
        # 1080p30「推荐」应落在 2-10 Mbps，低了糊、高了白送
        bps = D.bitrate_bps(1920, 1080, 30, "推荐")
        self.assertGreater(bps, 2_000_000)
        self.assertLess(bps, 10_000_000)


class TestFiltergraph(unittest.TestCase):
    def test_all_modes_never_stretch(self):
        for fit in D.FIT_MODES:
            g = D.build_filter(1080, 1920, 30, fit)
            self.assertIn("force_original_aspect_ratio", g, f"{fit} 必须保比例")
            self.assertNotIn("scale=1080:1920,", g,
                             f"{fit} 不能直接给定宽高硬缩（等于拉伸）")
            self.assertTrue(g.endswith("[v]"), f"{fit} 必须以 [v] 结尾")
            self.assertIn("format=yuv420p", g)

    def test_fps_applied_last(self):
        g = D.build_filter(1920, 1080, 25, "contain")
        self.assertLess(g.index("pad="), g.index("fps=25"),
                        "帧率必须在几何变换之后，否则 pad 会重新引入重复帧")

    def test_unknown_fit_raises(self):
        with self.assertRaises(ValueError):
            D.build_filter(1920, 1080, 30, "stretch")

    def test_no_negative_brightness(self):
        # limited range 黑=16，压暗会把暗部压到 0，播放器上会色偏
        self.assertNotIn("brightness=-", D.build_filter(1080, 1920, 30, "blur"))


class TestCheck(unittest.TestCase):
    def _master(self, **kw):
        m = {"path": "/m.mp4", "width": 1920, "height": 1080, "fps": 30.0,
             "vcodec": "h264", "pix_fmt": "yuv420p", "acodec": "aac",
             "duration": 19.0, "size": 1}
        m.update(kw)
        return m

    def _dst(self, **kw):
        d = {"path": "/o/bilibili__1920x1080.mp4", "width": 1920, "height": 1080,
             "fps": 30.0, "vcodec": "h264", "pix_fmt": "yuv420p",
             "acodec": "aac", "duration": 19.0, "size": 1}
        d.update(kw)
        return d

    def test_clean_pass(self):
        errors, warnings = D.check(self._master(), self._dst(),
                                   pp.profile_for("bilibili"), 1920, 1080)
        self.assertEqual(errors, [])
        self.assertTrue(any("assumed" in w for w in warnings))

    def test_wrong_dims(self):
        errors, _ = D.check(self._master(), self._dst(width=1080, height=1920),
                            pp.profile_for("bilibili"), 1920, 1080)
        self.assertTrue(any("分辨率" in e for e in errors))

    def test_wrong_fps(self):
        errors, _ = D.check(self._master(), self._dst(fps=25.0),
                            pp.profile_for("bilibili"), 1920, 1080)
        self.assertTrue(any("帧率" in e for e in errors))

    def test_no_audio_is_error(self):
        errors, _ = D.check(self._master(), self._dst(acodec=""),
                            pp.profile_for("bilibili"), 1920, 1080)
        self.assertTrue(any("音轨" in e for e in errors))

    def test_duration_drift(self):
        errors, _ = D.check(self._master(), self._dst(duration=17.0),
                            pp.profile_for("bilibili"), 1920, 1080)
        self.assertTrue(any("时长" in e for e in errors))

    def test_wrong_container(self):
        dst = self._dst(path="/o/x.mov")
        errors, _ = D.check(self._master(), dst, pp.profile_for("bilibili"),
                            1920, 1080)
        self.assertTrue(any("容器" in e for e in errors))


class TestPlan(unittest.TestCase):
    def test_naming_matches_jianying_convention(self):
        master = {"path": "/tmp/母版.mov", "width": 1920, "height": 1080,
                  "fps": 30.0, "vcodec": "h264", "pix_fmt": "yuv420p",
                  "acodec": "aac", "duration": 10.0, "size": 1}
        plan = D.plan_for(master, pp.profile_for("douyin"),
                          Path("/out"), "blur", "demo")
        self.assertEqual(plan.dst.name, "demo__douyin__1080x1920.mp4")

    def test_silent_master_gets_silent_track(self):
        master = {"path": "/tmp/m.mov", "width": 1920, "height": 1080,
                  "fps": 30.0, "vcodec": "h264", "pix_fmt": "yuv420p",
                  "acodec": "", "duration": 10.0, "size": 1}
        plan = D.plan_for(master, pp.profile_for("bilibili"), Path("/out"),
                          "contain", "demo")
        argv = plan.argv()
        self.assertIn("anullsrc", " ".join(argv))
        self.assertIn("-shortest", argv)


# ---------------------------------------------------------------- 真实编码
@unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg/ffprobe")
class TestRealEncode(unittest.TestCase):
    """用 testsrc2 + sine 生成母版，真跑 ffmpeg，再用亮度采样验几何。

    testsrc2 有彩色方块和移动元素，模糊背景后顶部不会是黑；央黑边则恰好是 16。
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="deliver-test-"))
        cls.master = cls.tmp / "master.mp4"
        r = subprocess.run(
            [D.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=1.5",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
             str(cls.master)], capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            raise unittest.SkipTest(f"生成母版失败: {r.stderr[-300:]}")
        cls.out = cls.tmp / "out"
        cls.blur = D.deliver(cls.master, ["douyin"], cls.out, fit="blur", quiet=True)
        cls.contain = D.deliver(cls.master, ["douyin"], cls.out, fit="contain",
                                stem="contain", quiet=True)
        cls.land = D.deliver(cls.master, ["bilibili"], cls.out, fit="contain",
                             quiet=True)
        cls.crop = D.deliver(cls.master, ["douyin"], cls.out, fit="crop",
                             stem="crop", quiet=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _path(self, evidence):
        return evidence["platforms"][0]["output"]

    def test_all_succeeded(self):
        for name in ("blur", "contain", "land", "crop"):
            ev = getattr(self, name)
            self.assertEqual(ev["failed"], 0,
                             f"{name} 失败: {ev['platforms'][0].get('errors')}")

    def test_landscape_spec_exact(self):
        dst = D.probe(self._path(self.land))
        self.assertEqual((dst["width"], dst["height"]), (1920, 1080))
        self.assertAlmostEqual(dst["fps"], 30.0, places=2)
        self.assertEqual(dst["acodec"], "aac")
        self.assertAlmostEqual(dst["duration"], 1.5, delta=0.25)

    def test_blur_fills_letterbox_area(self):
        p = self._path(self.blur)
        top = luma(p, 1080, 500, 0, 0)
        mid = luma(p, 1080, 500, 0, 710)
        self.assertGreater(top, 25, f"模糊背景不是内容（顶部亮度 {top:.1f}）")
        self.assertGreater(mid, 25, f"主体区没画面（中部亮度 {mid:.1f}）")

    def test_contain_has_black_bars(self):
        p = self._path(self.contain)
        top = luma(p, 1080, 500, 0, 0)
        mid = luma(p, 1080, 500, 0, 710)
        self.assertLess(top, 22, f"contain 顶部应为黑边，实测 {top:.1f}")
        self.assertGreater(mid, 25, f"contain 主体区没画面，实测 {mid:.1f}")

    def test_crop_has_no_bars(self):
        p = self._path(self.crop)
        top = luma(p, 1080, 500, 0, 0)
        self.assertGreater(top, 25, "crop 顶部应满画面，不该是黑边")

    def test_no_vertical_distortion(self):
        # 同比例（16:9→16:9）只该缩放，整幅平均亮度应和母版接近
        master = D.probe(self.master)
        self.assertAlmostEqual(master["width"] / master["height"], 16 / 9, places=3)
        m_luma = luma(self.master, master["width"], master["height"], 0, 0)
        d_luma = luma(self._path(self.land), 1920, 1080, 0, 0)
        self.assertLess(abs(m_luma - d_luma), 12,
                        f"同比例转码亮度偏移过大 {m_luma:.1f}->{d_luma:.1f}（可能拉伸/裁切）")

    def test_cross_platform_batch(self):
        ev = D.deliver(self.master, ["bilibili", "douyin", "xiaohongshu"],
                       self.tmp / "batch", fit="blur", quiet=True)
        self.assertEqual(ev["failed"], 0)
        self.assertEqual(len(ev["platforms"]), 3)
        for row in ev["platforms"]:
            self.assertTrue(Path(row["output"]).is_file())
            self.assertTrue(row["output"].rsplit(".", 1)[-1] == "mp4")


if __name__ == "__main__":
    unittest.main(verbosity=2)
