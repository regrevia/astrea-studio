"""本机任务里的素材引擎：搜、下、去重、dry-run 都不该靠网络跑测试。

这里打桩的是 `materials._download` 和 `materials.search_wikimedia`，
验的是 jobs.materials_search / jobs._materials 的接线与返回形状：
面板拿到的每行都得有 path（含 dryRun 的计划路径）、许可和 md5。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

MW_ROOT = Path(__file__).resolve().parents[1]
if str(MW_ROOT) not in sys.path:
    sys.path.insert(0, str(MW_ROOT))

import jobs  # noqa: E402
import materials as M  # noqa: E402


ITEM = {
    "provider": "wikimedia",
    "id": "12345",
    "title": "Cat playing with a toy",
    "url": "https://upload.wikimedia.org/cat.webm",
    "page": "https://commons.wikimedia.org/wiki/File:Cat_playing_with_a_toy.webm",
    "mime": "video/webm",
    "bytes": 2048,
    "license": "CC BY-SA 4.0",
    "author": "Someone",
}


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "library"
        self.jobs_dir = Path(self.tmp.name) / "jobs"
        self._old_jobs_dir = jobs.JOBS_DIR
        jobs.JOBS_DIR = self.jobs_dir
        self.addCleanup(lambda: setattr(jobs, "JOBS_DIR", self._old_jobs_dir))
        self.job = {"id": "260101-000000-abc123", "kind": "materials", "payload": {}}
        (self.jobs_dir / self.job["id"]).mkdir(parents=True, exist_ok=True)

        self.searched: list[tuple] = []

    def patch_search(self, rows):
        def fake(query, kind="video", limit=5, max_mb=60):
            self.searched.append((query, kind, limit, max_mb))
            return list(rows)
        old = M.search_wikimedia
        M.search_wikimedia = fake
        self.addCleanup(lambda: setattr(M, "search_wikimedia", old))

    def patch_download(self, payload=b"x" * 4096):
        def fake(url, dest, timeout=180):
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(payload)
            return len(payload)
        old = M._download
        M._download = fake
        self.addCleanup(lambda: setattr(M, "_download", old))


class SearchTest(_Base):
    def test_empty_query_rejected(self):
        with self.assertRaises(ValueError):
            jobs.materials_search("   ")

    def test_bad_kind_rejected(self):
        with self.assertRaises(ValueError):
            jobs.materials_search("cat", kind="audio")

    def test_passes_limits_and_returns_rows(self):
        self.patch_search([ITEM])
        rows = jobs.materials_search("cat", kind="image", n=99)
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.searched, [("cat", "image", 20, 60)])
        self.assertEqual(rows[0]["license"], "CC BY-SA 4.0")


class MaterialsJobTest(_Base):
    def test_query_downloads_and_records(self):
        self.patch_search([ITEM])
        self.patch_download()
        res = jobs._materials(self.job, {"query": "cat", "root": str(self.root)})
        self.assertEqual(res["engine"], "materials")
        self.assertEqual(res["count"], 1)
        item = res["items"][0]
        self.assertTrue(item["path"].startswith(str(self.root / "_assets" / "wikimedia")))
        self.assertTrue(Path(item["path"]).is_file())
        self.assertEqual(len(item["md5"]), 32)
        self.assertEqual(item["license"], "CC BY-SA 4.0")
        sidecar = Path(item["path"] + ".json")
        self.assertTrue(sidecar.is_file())
        self.assertIn("Cat playing", sidecar.read_text(encoding="utf-8"))

    def test_items_bypass_search(self):
        self.patch_search([ITEM])
        self.patch_download()
        res = jobs._materials(self.job, {"items": [ITEM], "root": str(self.root)})
        self.assertEqual(self.searched, [])
        self.assertEqual(res["count"], 1)

    def test_dry_run_writes_nothing(self):
        self.patch_search([ITEM])
        self.patch_download()
        res = jobs._materials(self.job, {"query": "cat", "root": str(self.root),
                                         "dryRun": True})
        self.assertTrue(res["dryRun"])
        item = res["items"][0]
        self.assertTrue(item["dryRun"])
        self.assertFalse(Path(item["path"]).exists())
        self.assertFalse(self.root.exists())

    def test_empty_search_is_a_readable_error(self):
        self.patch_search([])
        with self.assertRaises(ValueError) as ctx:
            jobs._materials(self.job, {"query": "no-such-material", "root": str(self.root)})
        self.assertIn("没搜到", str(ctx.exception))

    def test_missing_query_and_items_rejected(self):
        with self.assertRaises(ValueError):
            jobs._materials(self.job, {"root": str(self.root)})

    def test_second_identical_download_is_deduped(self):
        self.patch_search([ITEM])
        self.patch_download(b"same-bytes" * 300)
        first = jobs._materials(self.job, {"query": "cat", "root": str(self.root)})
        second = jobs._materials(self.job, {"query": "cat", "root": str(self.root)})
        self.assertNotIn("duplicate", first["items"][0])
        self.assertTrue(second["items"][0].get("duplicate"))
        self.assertEqual(second["items"][0]["path"], first["items"][0]["path"])

    def test_submit_requires_query_or_items(self):
        self.addCleanup(lambda: setattr(jobs, "_pump", jobs._pump))
        jobs._pump = lambda: None
        with self.assertRaises(ValueError):
            jobs.submit("materials", {"root": str(self.root)})


class DeliverablesTest(unittest.TestCase):
    """认领回来的成片在 out/ 下，也要出现在交付清单里（Web 的影片列表读这个接口）。"""

    def test_claim_outputs_are_listed_beside_render_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "deliver").mkdir()
            (root / "out" / "jobA" / "r1").mkdir(parents=True)
            (root / "deliver" / "a.mp4").write_bytes(b"x" * 16)
            (root / "out" / "jobA" / "r1" / "jobA__r1__douyin.mp4").write_bytes(b"y" * 32)
            saved = (jobs.DELIVER_DIR, jobs.CLAIM_DIR)
            jobs.DELIVER_DIR, jobs.CLAIM_DIR = root / "deliver", root / "out"
            try:
                rows = jobs.deliverables(10)
            finally:
                jobs.DELIVER_DIR, jobs.CLAIM_DIR = saved
            self.assertEqual(sorted(r["name"] for r in rows),
                             ["a.mp4", "jobA__r1__douyin.mp4"])
            self.assertEqual({r["name"]: r["source"] for r in rows}["jobA__r1__douyin.mp4"], "claim")


if __name__ == "__main__":
    unittest.main()

