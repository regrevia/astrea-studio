"""配乐行与拍点接口的边界：路径只能落在素材根目录里，越界就是没有。"""

import tempfile
import unittest
from pathlib import Path

import jobs


class MusicRowsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "bgm").mkdir()
        (self.root / "shots").mkdir()
        self._materials = jobs.MATERIALS_DIR
        jobs.MATERIALS_DIR = self.root
        jobs._music_beats_cache.clear()

    def tearDown(self) -> None:
        jobs.MATERIALS_DIR = self._materials
        jobs._music_beats_cache.clear()
        self._tmp.cleanup()

    def test_rows_list_audio_with_rel_and_kind(self) -> None:
        (self.root / "bgm" / "demo.m4a").write_bytes(b"\x00" * 64)
        (self.root / "bgm" / "notes.txt").write_bytes(b"x")
        rows = jobs.music_rows()
        self.assertEqual([r["rel"] for r in rows], ["bgm/demo.m4a"])
        self.assertEqual(rows[0]["kind"], "music")

    def test_beats_rejects_path_outside_materials(self) -> None:
        outside = self.root.parent / "outside.m4a"
        outside.write_bytes(b"\x00" * 64)
        try:
            with self.assertRaises(KeyError):
                jobs.music_beats("../outside.m4a")
        finally:
            outside.unlink()

    def test_beats_rejects_missing_and_non_audio(self) -> None:
        with self.assertRaises(KeyError):
            jobs.music_beats("bgm/nope.m4a")
        (self.root / "shots" / "clip.mp4").write_bytes(b"\x00" * 64)
        with self.assertRaises(KeyError):
            jobs.music_beats("shots/clip.mp4")


if __name__ == "__main__":
    unittest.main()
