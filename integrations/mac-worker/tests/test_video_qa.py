"""成片体检取样框与字幕时间窗：不跑 ffmpeg，只验算出来的东西对不对。用 unittest。"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import video_qa as qa  # noqa: E402

SUBTITLE_META = {
    "canvas": {"width": 1080, "height": 1920, "fps": 30},
    "text": [
        {"text": "第一句", "start": 0.0, "end": 2.0, "y": 0.86, "size": 0.052},
        {"text": "第二句", "start": 3.0, "end": 5.0, "y": 0.86, "size": 0.052},
    ],
}


class TextBandTest(unittest.TestCase):
    def test_factcard_without_rows_keeps_default_box(self):
        self.assertIsNone(qa._text_band({"canvas": {"width": 1080, "height": 1920}}))
        self.assertIsNone(qa._text_band({"text": []}))

    def test_subtitle_band_sits_on_the_subtitle_line(self):
        band = qa._text_band(SUBTITLE_META)
        self.assertIsNotNone(band)
        x0, y0, x1, y1 = band
        self.assertLess(x0, 1080 // 8)
        self.assertGreater(x1, 1080 * 3 // 4)
        # 字在画面 86% 高度处，取样带必须把这一行完整包住
        self.assertLess(y0, 0.86 * 1920)
        self.assertGreater(y1, 0.86 * 1920)

    def test_band_clamps_at_frame_edge(self):
        meta = {"canvas": {"width": 1080, "height": 1920},
                "text": [{"text": "贴边", "start": 0, "end": 1, "y": 0.98, "size": 0.052}]}
        _, _, _, y1 = qa._text_band(meta)
        self.assertLessEqual(y1, 1920)


class CoveredTest(unittest.TestCase):
    def test_marks_only_declared_spans(self):
        covered = qa._covered_by_text(SUBTITLE_META, 28)  # 0–6.75s，每点 0.25s
        self.assertIsNotNone(covered)
        self.assertTrue(covered[1])   # 0.25s，第一句里
        self.assertFalse(covered[10])  # 2.5s，两句之间
        self.assertTrue(covered[13])  # 3.25s，第二句里
        self.assertFalse(covered[24])  # 6.0s，第二句之后

    def test_no_rows_means_unbounded(self):
        self.assertIsNone(qa._covered_by_text({"canvas": {"width": 1080, "height": 1920}}, 10))


class GapFindingsTest(unittest.TestCase):
    def test_factcard_still_reports_whole_frame_gap(self):
        series = np.array([20.0, 20.0, 1.0, 1.0], dtype=float)
        got = qa._gap_findings("text_gap", series, None)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["code"], "text_gap")
        self.assertIn("卡面没字", got[0]["detail"])
        self.assertEqual(got[0]["at"], 0.5)

    def test_subtitle_blank_between_sentences_is_not_a_defect(self):
        # 整条片子一半时间没字，但那些时间本来就不属于任何一句
        series = np.array([20.0, 1.0, 20.0, 1.0], dtype=float)
        covered = np.array([True, False, True, False])
        self.assertEqual(qa._gap_findings("text_gap", series, covered), [])

    def test_subtitle_missing_inside_its_own_span_is_reported(self):
        series = np.array([20.0, 1.0, 1.0, 1.0], dtype=float)
        covered = np.array([True, True, True, True])
        got = qa._gap_findings("text_gap", series, covered)
        self.assertEqual(len(got), 1)
        self.assertIn("字幕没贴上", got[0]["detail"])

    def test_character_gap_ignores_coverage(self):
        series = np.array([20.0, 1.0, 1.0, 1.0], dtype=float)
        got = qa._gap_findings("character_gap", series, None)
        self.assertEqual(got[0]["code"], "character_gap")


if __name__ == "__main__":
    unittest.main()
