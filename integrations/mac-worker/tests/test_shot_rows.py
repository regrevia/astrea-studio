"""素材清单里的视频规格：面板按真实时长排片，探不到就只报体积，不编时长。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jobs  # noqa: E402


class ShotRowsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "shots" / "pack").mkdir(parents=True)
        (self.tmp / "shots" / "pack" / "clip.mp4").write_bytes(b"not really a video")
        (self.tmp / "shots" / "pack" / "cover.png").write_bytes(b"png")
        self.old_root = jobs.MATERIALS_DIR
        jobs.MATERIALS_DIR = self.tmp
        jobs._probe_cache.clear()

    def tearDown(self):
        jobs.MATERIALS_DIR = self.old_root
        jobs._probe_cache.clear()

    def test_rel_and_kind(self):
        rows = {row["rel"]: row for row in jobs.shot_rows()}
        self.assertEqual(rows["shots/pack/clip.mp4"]["kind"], "video")
        self.assertEqual(rows["shots/pack/cover.png"]["kind"], "image")

    def test_probe_failure_keeps_row_without_duration(self):
        rows = jobs.shot_rows()
        clip = next(row for row in rows if row["rel"].endswith("clip.mp4"))
        self.assertNotIn("duration", clip)

    def test_probe_result_cached_by_mtime(self):
        payload = {
            "streams": [{"width": 1600, "height": 1000, "r_frame_rate": "30/1"}],
            "format": {"duration": "7.13"},
        }
        ok = mock.Mock(returncode=0, stdout=json.dumps(payload).encode())
        with mock.patch.object(jobs.subprocess, "run", return_value=ok) as run:
            first = jobs.shot_rows()
            second = jobs.shot_rows()
        self.assertEqual(run.call_count, 1)
        clip = next(row for row in first if row["rel"].endswith("clip.mp4"))
        self.assertEqual(clip["duration"], 7.13)
        self.assertEqual(clip["width"], 1600)
        self.assertEqual(clip["fps"], 30.0)
        self.assertEqual(second, first)


if __name__ == "__main__":
    unittest.main()
