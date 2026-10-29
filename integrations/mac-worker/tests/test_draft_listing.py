"""draft_listing 的离线用例：不连剪映、不碰真实草稿索引，全部在临时目录里跑。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "export"))

import draft_listing as DL  # noqa: E402


def make_root(tmp: Path) -> tuple[Path, Path]:
    """造一个最小草稿索引 + 两个草稿目录。"""
    root = tmp / "com.lveditor.draft"
    root.mkdir()
    a = root / "studio_a__r1"
    a.mkdir()
    (a / "draft_meta_info.json").write_text(json.dumps({"draft_name": "studio_a__r1"}), encoding="utf-8")
    idx = {"all_draft_store": [
        {"draft_name": "studio_a__r1", "draft_fold_path": str(a),
         "tm_draft_modified": 1_700_000_000_000_000},
        {"draft_name": "other", "draft_fold_path": str(root / "missing"),
         "tm_draft_modified": 1_700_000_000_000_001},
    ]}
    (root / "root_meta_info.json").write_text(json.dumps(idx), encoding="utf-8")
    return root, a


class SurfaceTest(unittest.TestCase):
    def test_bump_makes_entry_newest(self):
        with tempfile.TemporaryDirectory() as d:
            root, a = make_root(Path(d))
            r = DL.surface("studio_a__r1", root=root, now_us=1_800_000_000_000_000)
            self.assertTrue(r["changed"])
            self.assertEqual(r["reason"], "ok")
            idx = json.loads((root / "root_meta_info.json").read_text(encoding="utf-8"))
            e = DL.find_entry(idx, "studio_a__r1")
            self.assertEqual(e["tm_draft_modified"], 1_800_000_000_000_000)
            # 目录里的 meta 也同步（排序不靠它，但不该继续留旧值）
            meta = json.loads((a / "draft_meta_info.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["tm_draft_modified"], 1_800_000_000_000_000)

    def test_backup_is_original_and_kept(self):
        with tempfile.TemporaryDirectory() as d:
            root, _ = make_root(Path(d))
            original = (root / "root_meta_info.json").read_text(encoding="utf-8")
            DL.surface("studio_a__r1", root=root, now_us=1_800_000_000_000_000)
            DL.surface("studio_a__r1", root=root, now_us=1_900_000_000_000_000)
            bak = (root / "root_meta_info.json.studio-bak").read_text(encoding="utf-8")
            self.assertEqual(bak, original)

    def test_unknown_draft_reports_not_indexed(self):
        with tempfile.TemporaryDirectory() as d:
            root, _ = make_root(Path(d))
            r = DL.surface("nope", root=root)
            self.assertFalse(r["changed"])
            self.assertEqual(r["reason"], "draft_not_indexed")
            self.assertIsNone(r["backup"])

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as d:
            root, _ = make_root(Path(d))
            before = (root / "root_meta_info.json").read_text(encoding="utf-8")
            r = DL.surface("studio_a__r1", root=root, now_us=1_800_000_000_000_000, dry=True)
            self.assertTrue(r["changed"])
            self.assertEqual((root / "root_meta_info.json").read_text(encoding="utf-8"), before)

    def test_find_by_path(self):
        with tempfile.TemporaryDirectory() as d:
            root, a = make_root(Path(d))
            idx = json.loads((root / "root_meta_info.json").read_text(encoding="utf-8"))
            e = DL.find_entry(idx, draft_path=str(a))
            self.assertEqual(e["draft_name"], "studio_a__r1")

    def test_monotonic_even_with_future_stamp(self):
        with tempfile.TemporaryDirectory() as d:
            root, _ = make_root(Path(d))
            DL.surface("studio_a__r1", root=root, now_us=2_000_000_000_000_000)
            r = DL.surface("studio_a__r1", root=root, now_us=1_000_000_000_000_000)
            self.assertGreater(r["after_us"], r["before_us"])

    def test_restore_returns_backup(self):
        with tempfile.TemporaryDirectory() as d:
            root, _ = make_root(Path(d))
            original = (root / "root_meta_info.json").read_text(encoding="utf-8")
            DL.surface("studio_a__r1", root=root, now_us=1_800_000_000_000_000)
            r = DL.restore(root=root)
            self.assertTrue(r["restored"])
            self.assertEqual((root / "root_meta_info.json").read_text(encoding="utf-8"), original)

    def test_restore_without_backup_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            root, _ = make_root(Path(d))
            r = DL.restore(root=root)
            self.assertFalse(r["restored"])
            self.assertEqual(r["reason"], "no_backup")

    def test_stale_entries_and_drop(self):
        with tempfile.TemporaryDirectory() as d:
            root, _ = make_root(Path(d))
            self.assertEqual([e["name"] for e in DL.stale_entries(root=root)], ["other"])
            r = DL.drop_entry("other", root=root)
            self.assertEqual(r["removed"], 1)
            self.assertEqual(r["entries"], 1)
            self.assertEqual(DL.stale_entries(root=root), [])


class VisibleTitlesTest(unittest.TestCase):
    def test_parses_tree_dump(self):
        dump = "\n".join([
            "1\tAXWindow\t剪映专业版\t\t\t\t\t-1290,1226 1168x780",
            "5\tAXImage\tHomePageDraftTitle:studio_a__r1\t\t\t\t\t100,200 98x21",
            "6\tAXImage\tHomePageDraftTitle:studio_a__r1\t\t\t\t\t100,200 98x21",
            "7\tAXImage\tHomePageDraftTitle:b\t\t\t\t\t100,300 98x21",
            "",
        ])
        self.assertEqual(DL.visible_titles(dump), ["studio_a__r1", "b"])

    def test_empty(self):
        self.assertEqual(DL.visible_titles(""), [])


if __name__ == "__main__":
    unittest.main()
