#!/usr/bin/env python3
"""worker 的 render_timeline 分派测试。直接跑：python3 test_worker_timeline.py

只测「分派 + 产物证据」这一段：不连 API、不碰 S3（payload 不带 runId 即本地试跑），
所以它是纯离线可跑的。真正的上传/草稿投递要靠平台侧联调。
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
import worker as W  # noqa: E402
from config import WorkerConfig  # noqa: E402
from export import deliver as D  # noqa: E402
from export import timeline_render as T  # noqa: E402

HAS_FFMPEG = bool(shutil.which(T.FFMPEG))


class _NoProgress:
    def __init__(self):
        self.calls = []

    def __call__(self, pct, stage):
        self.calls.append((pct, stage))


@unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
class TestTimelineDispatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="wk-tl-"))
        asset = cls.tmp / "asset"
        asset.mkdir()
        for name, color in (("a.mp4", "white"), ("b.mp4", "black")):
            subprocess.run(
                [T.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", f"color=c={color}:s=320x180:d=2:r=30",
                 "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                 str(asset / name)], check=True)
        cls.spec = {
            "canvas": {"width": 320, "height": 180, "fps": 30},
            "video": [{"src": str(asset / "a.mp4"), "in": 0, "out": 2},
                      {"src": str(asset / "b.mp4"), "in": 0, "out": 2}],
            "text": [{"text": "标题", "start": 0.2, "end": 1.0,
                      "x": 0.5, "y": 0.5, "size": 0.2}],
            "audio": [],
        }
        cls.w = W.Worker(WorkerConfig())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_master_only(self):
        prog = _NoProgress()
        out = self.w.do_timeline("job-a", {"spec": self.spec}, prog)
        self.assertEqual(out["engine"], "timeline")
        self.assertAlmostEqual(out["duration"], 4.0, delta=0.1)
        self.assertEqual((out["canvas"]["width"], out["canvas"]["height"]), (320, 180))
        self.assertEqual(out["deliveries"], [])
        self.assertNotIn("videoKey", out, "本地试跑不应上传 S3")
        p = D.probe(out["videoPath"])
        self.assertEqual(p["vcodec"], "h264")
        self.assertEqual(p["acodec"], "aac")
        self.assertIn((10, "timeline:rendering"), prog.calls, "必须上报进度阶段")

    def test_master_plus_platforms(self):
        out = self.w.do_timeline(
            "job-b", {"spec": self.spec, "platforms": ["douyin", "bilibili"],
                      "fit": "blur"},
            _NoProgress())
        self.assertEqual(len(out["deliveries"]), 2)
        self.assertEqual(out.get("deliveryFailed", 0), 0)
        by = {d["platform"]: d for d in out["deliveries"]}
        self.assertTrue(by["douyin"]["ok"], by["douyin"].get("errors"))
        self.assertEqual(by["douyin"]["probe"]["width"] * 16,
                         by["douyin"]["probe"]["height"] * 9)
        self.assertTrue(Path(by["douyin"]["output"]).exists())
        self.assertEqual(by["bilibili"]["probe"]["width"] * 9,
                         by["bilibili"]["probe"]["height"] * 16)

    def test_platform_run_spec_shape(self):
        """平台侧 run.spec 是外壳：{engine:'timeline', platforms:[...], timeline:{...}}。"""
        spec = json.loads(json.dumps(self.spec))
        out = self.w.do_timeline(
            "job-nested",
            {"spec": {"engine": "timeline", "platforms": ["douyin"],
                      "timeline": spec}},
            _NoProgress())
        self.assertEqual(len(out["deliveries"]), 1)
        self.assertTrue(out["deliveries"][0]["ok"])
        self.assertEqual(out["deliveries"][0]["platform"], "douyin")

    def test_bad_spec_reports_readably(self):
        with self.assertRaises(W.TimelineError) as cm:
            self.w.do_timeline("job-c", {"spec": {"canvas": {"width": 320}}},
                               _NoProgress())
        self.assertIn("spec", str(cm.exception))

    def test_missing_spec_rejected(self):
        with self.assertRaises(W.TimelineError):
            self.w.do_timeline("job-d", {}, _NoProgress())

    def test_asset_base_used_for_relative_src(self):
        spec = json.loads(json.dumps(self.spec))
        spec["video"] = [{"src": "a.mp4", "in": 0, "out": 1}]
        out = self.w.do_timeline(
            "job-e",
            {"spec": spec, "assetBase": str(self.tmp / "asset")},
            _NoProgress())
        self.assertAlmostEqual(out["duration"], 1.0, delta=0.1)

    def test_spec_lands_on_disk_for_replay(self):
        self.w.do_timeline("job-f", {"spec": self.spec}, _NoProgress())
        from config import STUDIO_HOME
        sp = STUDIO_HOME / "timeline" / "job-f" / "spec.json"
        self.assertTrue(sp.exists(), "spec 必须落盘，否则出问题无法复现")
        on_disk = json.loads(sp.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["canvas"]["width"], 320)


if __name__ == "__main__":
    unittest.main(verbosity=2)
