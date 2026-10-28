"""剪映草稿的音轨收口：music 与 bgm 两种写法都要落到同一条 BGM 轨。"""
import unittest

import jianying_engine as J


class AudioTrackSpecsTest(unittest.TestCase):
    def test_music_only_becomes_bgm_track(self):
        rows = J._audio_track_specs({"music": {"src": "/tmp/demo.m4a", "volume": 0.4}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "bgm")
        self.assertEqual(rows[0]["path"], "/tmp/demo.m4a")
        self.assertEqual(rows[0]["volume"], 0.4)
        self.assertTrue(rows[0]["loop"])

    def test_bgm_still_wins_and_is_not_duplicated(self):
        rows = J._audio_track_specs({
            "bgm": "/tmp/a.m4a",
            "music": {"src": "/tmp/b.m4a"},
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["path"], "/tmp/a.m4a")

    def test_music_alongside_voiceover(self):
        rows = J._audio_track_specs({
            "music": "/tmp/bgm.m4a",
            "voiceover": "/tmp/vo.mp3",
        })
        self.assertEqual([r["kind"] for r in rows], ["bgm", "voiceover"])


if __name__ == "__main__":
    unittest.main()
