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

    def test_project_dir_has_readable_slug_and_id(self):
        row = lib.land(self.src, project="账号诊断", project_id="6ae235e6aa561aad282987e0",
                       title="三屏版", root=self.root)
        p = Path(row["path"])
        self.assertEqual(p.parent.parent.name, "账号诊断__6ae235e6")
        self.assertIn("__账号诊断__6ae235e6__", p.name)
        self.assertEqual(row["projectId"], "6ae235e6aa561aad282987e0")
        self.assertEqual(row["projectDir"], "账号诊断__6ae235e6")
        self.assertEqual(row["project"], "账号诊断")
        self.assertEqual(lib.project_dir_name("账号诊断"), "账号诊断")

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

    def test_migrate_old_id_dir(self):
        old = self.root / "6abc12345678901234567890" / "2026-10"
        old.mkdir(parents=True)
        pid = "6abc12345678901234567890"
        name = "20261001-120000__%s__001__旧片__douyin__r1.mp4" % pid
        (old / name).write_bytes(FAKE_MP4)
        (old / name.replace(".mp4", ".json")).write_text(json.dumps(
            {"assetId": name[:-4], "path": str(old / name), "project": pid,
             "title": "旧片", "revision": 1}, ensure_ascii=False), encoding="utf-8")
        (self.root / "index.jsonl").write_text(json.dumps(
            {"assetId": name[:-4], "path": str(old / name), "project": pid,
             "title": "旧片", "revision": 1, "bytes": len(FAKE_MP4)},
            ensure_ascii=False) + "\n", encoding="utf-8")

        moved = lib.migrate_dir(self.root, pid, "账号诊断", project_id=pid, dry_run=True)
        self.assertEqual(len(moved), 2)
        self.assertTrue((old / name).is_file(), "预演不能动文件")

        moved = lib.migrate_dir(self.root, pid, "账号诊断", project_id=pid)
        self.assertEqual(len(moved), 2)
        newdir = self.root / "账号诊断__6abc1234"
        newfile = newdir / "2026-10" / name.replace("__%s__" % pid, "__账号诊断__6abc1234__")
        self.assertTrue(newfile.is_file())
        self.assertFalse((self.root / pid).exists())
        row = json.loads((newfile.with_suffix(".json")).read_text(encoding="utf-8"))
        self.assertEqual(row["path"], str(newfile))
        self.assertEqual(row["projectDir"], "账号诊断__6abc1234")
        idx = json.loads((self.root / "index.jsonl").read_text(encoding="utf-8").strip())
        self.assertEqual(idx["path"], str(newfile))
        self.assertEqual(idx["projectId"], pid)
        self.assertEqual(lib.verify(root=self.root), [])

    def test_migrate_studio_by_run_id(self):
        legacy = self.root / "studio" / "2026-09"
        legacy.mkdir(parents=True)
        name = "20260901-120000__studio__001__老片__douyin__r1.mp4"
        (legacy / name).write_bytes(FAKE_MP4)
        (legacy / name.replace(".mp4", ".json")).write_text(json.dumps(
            {"assetId": name[:-4], "path": str(legacy / name), "project": "studio",
             "runId": "run-9", "bytes": len(FAKE_MP4)}, ensure_ascii=False), encoding="utf-8")
        (self.root / "index.jsonl").write_text(json.dumps(
            {"assetId": name[:-4], "path": str(legacy / name), "project": "studio",
             "runId": "run-9", "bytes": len(FAKE_MP4)}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        rmap = {"run-9": {"name": "账号诊断", "id": "6abc12345678901234567890"}}
        res = lib.migrate_studio(self.root, rmap, dry_run=True)
        self.assertEqual((len(res["moved"]), len(res["kept"])), (1, 0))
        self.assertTrue((legacy / name).is_file())

        res = lib.migrate_studio(self.root, rmap)
        self.assertEqual(len(res["moved"]), 1)
        newfile = self.root / "账号诊断__6abc1234" / "2026-09" / name.replace("__studio__", "__账号诊断__6abc1234__")
        self.assertTrue(newfile.is_file())
        self.assertFalse((self.root / "studio").exists())
        idx = json.loads((self.root / "index.jsonl").read_text(encoding="utf-8").strip())
        self.assertEqual(idx["path"], str(newfile))
        self.assertEqual(idx["projectDir"], "账号诊断__6abc1234")
        self.assertEqual(lib.verify(root=self.root), [])

    def test_migrate_studio_keeps_unknown(self):
        legacy = self.root / "studio" / "2026-09"
        legacy.mkdir(parents=True)
        (legacy / "a.mp4").write_bytes(FAKE_MP4)
        res = lib.migrate_studio(self.root, {})
        self.assertEqual(res["moved"], [])
        self.assertEqual(len(res["kept"]), 1)
        self.assertTrue((legacy / "a.mp4").is_file())

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
