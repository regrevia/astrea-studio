"""素材获取的离线用例：不联网，把下载器换成假的。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

MW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MW))
import materials as M  # noqa: E402


def fake_download(payload: bytes):
    def _dl(url, dest, timeout=180):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        return len(payload)

    return _dl


ITEM = {"provider": "wikimedia", "id": "12345678", "title": "Cat playing",
        "url": "https://upload.wikimedia.org/x/cat.webm", "bytes": 4096,
        "license": "CC BY-SA 4.0", "author": "someone"}


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = M._download

    def tearDown(self):
        M._download = self._old
        self.tmp.cleanup()

    def _land_one(self, payload=b"x" * 4096, **kw):
        M._download = fake_download(payload)
        return M.fetch([dict(ITEM)], self.root, "video", **kw)

    def test_lands_file_sidecar_and_naming(self):
        rows = self._land_one()
        dest = Path(rows[0]["path"])
        self.assertTrue(dest.is_file())
        self.assertTrue(dest.parent.name == self.root.name or "_assets" in str(dest))
        self.assertIn("_assets/wikimedia/", str(dest))
        self.assertTrue(dest.name.startswith("cat-playing__wikimedia__"))
        side = dest.with_suffix(dest.suffix + ".json")
        meta = json.loads(side.read_text(encoding="utf-8"))
        for key in ("provider", "license", "author", "md5", "bytes", "fetchedAt", "kind"):
            self.assertIn(key, meta)
        self.assertEqual(meta["kind"], "video")

    def test_same_content_is_deduped_to_one_file(self):
        first = self._land_one()
        second = self._land_one()
        self.assertFalse(first[0].get("duplicate"))
        self.assertTrue(second[0].get("duplicate"))
        self.assertEqual(first[0]["path"], second[0]["path"])
        files = [p for p in self.root.glob("_assets/**/*.webm")]
        self.assertEqual(len(files), 1)

    def test_no_dedupe_keeps_second_copy(self):
        self._land_one()
        rows = self._land_one(dedupe=False)
        self.assertFalse(rows[0].get("duplicate"))
        self.assertEqual(len(list(self.root.glob("_assets/**/*.webm"))), 2)

    def test_dry_run_writes_nothing(self):
        rows = self._land_one(dry_run=True)
        self.assertTrue(rows[0]["dryRun"])
        self.assertFalse(Path(rows[0]["path"]).exists())


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _seed(self, payload=b"data" * 1024, md5=None):
        d = self.root / "_assets/wikimedia/2026-10"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "a.webm"
        f.write_bytes(payload)
        side = f.with_suffix(f.suffix + ".json")
        side.write_text(json.dumps({"path": str(f), "md5": md5 or M._md5(f), "bytes": len(payload)},
                                   ensure_ascii=False), encoding="utf-8")
        return f, side

    def test_healthy(self):
        self._seed()
        self.assertEqual(M.verify(self.root), [])

    def test_md5_mismatch(self):
        self._seed(md5="0" * 32)
        problems = M.verify(self.root)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["problem"], "md5 不符")

    def test_missing_file(self):
        f, _ = self._seed()
        f.unlink()
        self.assertEqual(M.verify(self.root)[0]["problem"], "文件缺失")

    def test_empty_file(self):
        self._seed(payload=b"")
        self.assertEqual(M.verify(self.root)[0]["problem"], "空文件")


class FindDuplicateTest(unittest.TestCase):
    def test_matches_only_live_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "_assets/wikimedia/2026-10"
            d.mkdir(parents=True)
            f = d / "a.webm"
            f.write_bytes(b"abc")
            md5 = M._md5(f)
            side = f.with_suffix(".webm.json")
            side.write_text(json.dumps({"path": str(f), "md5": md5}), encoding="utf-8")
            self.assertEqual(M.find_duplicate(root, md5)["path"], str(f))
            self.assertIsNone(M.find_duplicate(root, "f" * 32))
            f.unlink()
            self.assertIsNone(M.find_duplicate(root, md5))


if __name__ == "__main__":
    unittest.main()
