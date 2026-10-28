"""成片库回归：命名、目录、防覆盖、台账对账。用 unittest，不依赖 pytest。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import library as lib  # noqa: E402

FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"x" * 2048


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "raw.mp4"
        self.src.write_bytes(FAKE_MP4)
        self.root = self.tmp / "lib"

    def test_slugify_keeps_chinese(self):
        self.assertEqual(lib.slugify("账号诊断：三屏版 v2"), "账号诊断-三屏版-v2")
        self.assertEqual(lib.slugify("   "), "untitled")
        self.assertLessEqual(len(lib.slugify("x" * 80)), lib.SLUG_MAX)

    def test_landing_shape(self):
        row = lib.land(self.src, project="落地页", title="三屏讲清账号诊断",
                       platform="douyin", revision=2, run_id="r1", job_id="j1",
                       duration=30.833, root=self.root)
        p = Path(row["path"])
        self.assertTrue(p.is_file())
        self.assertFalse(self.src.exists(), "入库应移动，不是复制")
        self.assertEqual(p.parent.parent.name, "落地页")
        self.assertEqual(p.name.count("__"), 5, p.name)
        self.assertIn("__r2.mp4", p.name)
        self.assertIn("__douyin__", p.name)
        self.assertTrue(p.with_suffix(".json").is_file())
        rows = (self.root / "index.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0])["md5"], row["md5"])
        self.assertEqual(row["bytes"], len(FAKE_MP4))

    def test_no_overwrite(self):
        a = lib.land(self.src, project="p", title="同一条", platform="douyin",
                     revision=1, root=self.root)
        self.src.write_bytes(FAKE_MP4 + b"y")
        b = lib.land(self.src, project="p", title="同一条", platform="douyin",
                     revision=1, root=self.root)
        self.assertNotEqual(a["path"], b["path"])
        self.assertEqual(len(lib.list_assets("p", root=self.root)), 2)

    def test_revisions_coexist(self):
        r1 = lib.land(self.src, project="p", title="t", revision=1, root=self.root)
        self.src.write_bytes(FAKE_MP4 + b"z")
        r2 = lib.land(self.src, project="p", title="t", revision=2, root=self.root)
        self.assertEqual((r1["revision"], r2["revision"]), (1, 2))
        self.assertTrue(Path(r1["path"]).is_file())

    def test_verify(self):
        row = lib.land(self.src, project="p", title="t", root=self.root)
        self.assertEqual(lib.verify(self.root), [])
        Path(row["path"]).unlink()
        self.assertTrue(any("缺文件" in x for x in lib.verify(self.root)))

    def test_rejects_non_video(self):
        bad = self.tmp / "a.txt"
        bad.write_text("x")
        with self.assertRaises(ValueError):
            lib.land(bad, project="p", title="t", root=self.root)


if __name__ == "__main__":
    unittest.main()
