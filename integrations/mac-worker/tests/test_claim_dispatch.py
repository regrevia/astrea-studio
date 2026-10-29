"""认领分发（人工导出 → 平台入库）的离线用例：不连后端、不开剪映。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

MW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MW))

import claim_dispatch as claim  # noqa: E402


class BuildArgvTest(unittest.TestCase):
    def test_maps_every_required_flag(self):
        argv = claim.build_argv(
            "/usr/bin/python3", Path("/tmp/x/jianying_export.py"),
            claim_path="/tmp/out/r1.mp4", job_id="job1", revision="2",
            platform="douyin", outdir="/tmp/out", draft_path="/drafts/d__r1",
        )
        self.assertEqual(argv[0], "/usr/bin/python3")
        self.assertEqual(argv[2:4], ["--claim", "/tmp/out/r1.mp4"])
        self.assertIn("--job-id", argv)
        self.assertEqual(argv[argv.index("--job-id") + 1], "job1")
        self.assertEqual(argv[argv.index("--revision") + 1], "2")
        self.assertEqual(argv[argv.index("--platform") + 1], "douyin")
        self.assertEqual(argv[argv.index("--draft-path") + 1], "/drafts/d__r1")
        self.assertNotIn("--canvas", argv)

    def test_canvas_is_optional_and_passthrough(self):
        argv = claim.build_argv(
            "py", Path("/s.py"), claim_path="/a.mp4", job_id="j", revision=1,
            platform="douyin", outdir="/o", draft_path="/d", canvas="1080x1920",
        )
        self.assertEqual(argv[argv.index("--canvas") + 1], "1080x1920")


class FailureTest(unittest.TestCase):
    def test_known_codes_map_to_named_reasons(self):
        for rc, code in ((6, "export_probe_failed"),
                         (8, "export_output_exists"),
                         (11, "export_draft_revision_mismatch"),
                         (12, "draft_media_changed")):
            reason, message = claim.failure(rc, "boom")
            self.assertEqual(reason, code)
            self.assertIn("rc=%d" % rc, message)
            self.assertIn("boom", message)

    def test_unknown_code_falls_back_without_swallowing_output(self):
        reason, message = claim.failure(1, "Traceback ...")
        self.assertEqual(reason, "jianying_export_failed")
        self.assertIn("Traceback", message)

    def test_empty_tail_still_readable(self):
        reason, message = claim.failure(6, "")
        self.assertEqual(reason, "export_probe_failed")
        self.assertIn("无输出", message)


class FindVideoTest(unittest.TestCase):
    def test_picks_only_video_suffixes(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "r1__douyin.json").write_text("{}", encoding="utf-8")
            (d / "note.txt").write_text("x", encoding="utf-8")
            self.assertIsNone(claim.find_video(d))
            (d / "job__r1__douyin.mp4").write_bytes(b"v")
            self.assertTrue(claim.find_video(d).endswith(".mp4"))
            (d / "job__r1__douyin.mov").write_bytes(b"v")
            self.assertTrue(claim.find_video(d).endswith(".mov"))

    def test_missing_dir_is_not_an_error(self):
        self.assertIsNone(claim.find_video("/nonexistent/claim/dir"))


if __name__ == "__main__":
    unittest.main()
