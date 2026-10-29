import json
import tempfile
import unittest
from pathlib import Path

from export.draft_inspect import inspect


def _draft(tmp: Path, info: dict | None = None, *, encrypted: bool = False) -> Path:
    d = tmp / "draft_x__r1"
    d.mkdir()
    if encrypted:
        (d / "draft_info.json").write_bytes(b"\x1a\x9c\x00not-json")
    elif info is not None:
        (d / "draft_info.json").write_text(json.dumps(info), encoding="utf-8")
    return d


class DraftInspectTest(unittest.TestCase):
    def test_reads_canvas_duration_and_material_counts(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            d = _draft(tmp, {
                "canvas_config": {"width": 1080, "height": 1920},
                "duration": 30_000_000,
                "fps": 30,
                "tracks": [{}, {}],
                "materials": {"videos": [{"path": "assets/a.png"}], "audios": [{"path": "assets/b.mp3"}], "images": []},
            })
            (d / "assets").mkdir()
            (d / "assets/a.png").write_bytes(b"x")
            (d / "assets/b.mp3").write_bytes(b"x")
            info = inspect(d)
        self.assertTrue(info["infoReadable"])
        self.assertFalse(info["encrypted"])
        self.assertEqual(info["canvas"], {"width": 1080, "height": 1920})
        self.assertAlmostEqual(info["durationSec"], 30.0)
        self.assertEqual(info["fps"], 30)
        self.assertEqual(info["trackCount"], 2)
        self.assertEqual(info["media"], {"videos": 1, "audios": 1, "images": 0})
        self.assertEqual(info["missingMedia"], [])

    def test_reports_missing_media_without_guessing(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            d = _draft(tmp, {
                "materials": {"videos": [{"path": "assets/gone.png"}, {"path": "/nope/abs.mp4"}]},
            })
            info = inspect(d)
        self.assertEqual(info["missingMedia"], ["assets/gone.png", "/nope/abs.mp4"])

    def test_encrypted_draft_is_reported_not_failed(self):
        with tempfile.TemporaryDirectory() as td:
            d = _draft(Path(td), encrypted=True)
            info = inspect(d)
        self.assertTrue(info["encrypted"])
        self.assertFalse(info["infoReadable"])
        self.assertIsNone(info["canvas"])
        self.assertEqual(info["missingMedia"], [])

    def test_missing_directory_is_not_an_error(self):
        info = inspect(Path("/tmp/definitely-not-a-draft-xyz"))
        self.assertFalse(info["isDir"])
        self.assertFalse(info["infoReadable"])

    def test_schema_stamp_is_surfaced_when_present(self):
        with tempfile.TemporaryDirectory() as td:
            d = _draft(Path(td), {"materials": {}})
            (d / "draft_version.json").write_text(json.dumps({
                "app": "11.6.0", "schema": {"version": 360000, "source": "reference-same-major"},
            }), encoding="utf-8")
            info = inspect(d)
        self.assertEqual(info["schema"]["version"], 360000)
        self.assertEqual(info["schema"]["source"], "reference-same-major")
        self.assertEqual(info["schema"]["app"], "11.6.0")


if __name__ == "__main__":
    unittest.main()
