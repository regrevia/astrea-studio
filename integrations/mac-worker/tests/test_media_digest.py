import sys
import tempfile
import unittest
from pathlib import Path

from export.media_digest import digest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "export"))
import jianying_export as X  # noqa: E402


class MediaDigestTest(unittest.TestCase):
    def test_same_path_with_new_content_changes_the_digest(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "assets").mkdir()
            f = d / "assets" / "a.png"
            f.write_bytes(b"one")
            first = digest(["assets/a.png"], d)
            f.write_bytes(b"two-longer")
            self.assertNotEqual(first, digest(["assets/a.png"], d))

    def test_missing_file_is_marked_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "assets").mkdir()
            present = d / "assets" / "a.png"
            present.write_bytes(b"one")
            self.assertNotEqual(digest(["assets/a.png"], d), digest(["assets/b.png"], d))

    def test_urls_and_empty_refs_do_not_count(self):
        self.assertEqual(digest(["", "https://x/y.mp4"]), "")


class ExportMediaDriftTest(unittest.TestCase):
    def _draft(self, td: Path) -> Path:
        d = td / "draft_x__r1"
        (d / "assets").mkdir(parents=True)
        (d / "assets" / "a.png").write_bytes(b"one")
        (d / "draft_info.json").write_text(
            '{"canvas_config":{"width":1080,"height":1920},"duration":30000000,'
            '"fps":30,"tracks":[{}],"materials":{"videos":[{"path":"assets/a.png"}]}}',
            encoding="utf-8")
        return d

    def test_matching_digest_is_clean(self):
        with tempfile.TemporaryDirectory() as td:
            d = self._draft(Path(td))
            side = {"mediaDigest": digest(["assets/a.png"], d)}
            self.assertEqual(X.media_drift(str(d), side), "")

    def test_changed_asset_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            d = self._draft(Path(td))
            side = {"mediaDigest": digest(["assets/a.png"], d)}
            (d / "assets" / "a.png").write_bytes(b"replaced-with-other-bytes")
            self.assertTrue(X.media_drift(str(d), side).startswith("media "))

    def test_old_sidecar_without_digest_is_clean(self):
        with tempfile.TemporaryDirectory() as td:
            d = self._draft(Path(td))
            self.assertEqual(X.media_drift(str(d), {}), "")


if __name__ == "__main__":
    unittest.main()
