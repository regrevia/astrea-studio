"""draft_guard 的离线用例：临时草稿根，不碰真实草稿、不启动剪映、不写真实流水账。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "export"))

import draft_guard as DG  # noqa: E402
import plain_guard as PG  # noqa: E402

ENCRYPTED = b"GA2Jp0QhZ2VuZXJhdGVkLWJ5LWppYW55aW5nLWVuY3J5cHRlZA=="


def make_draft(root: Path, name: str = "studio_a__r1") -> Path:
    target = root / name
    target.mkdir()
    (target / "draft_info.json").write_text(
        json.dumps({"canvases": [{"width": 1080, "height": 1920}]}), encoding="utf-8")
    (target / "draft_meta_info.json").write_text(
        json.dumps({"draft_name": name}), encoding="utf-8")
    (target / "studio_export_meta.json").write_text(
        json.dumps({"fps": 30}), encoding="utf-8")
    return target


def encrypt_in_place(target: Path) -> None:
    (target / "draft_info.json").write_bytes(ENCRYPTED)
    (target / "draft_meta_info.json").write_bytes(ENCRYPTED)


class InspectTest(unittest.TestCase):
    def test_plain_draft_gets_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = make_draft(root)
            event = DG.inspect(target, settle=0)
            self.assertEqual(event["action"], "snapshot")
            self.assertTrue(PG.read_manifest(target))

    def test_second_pass_is_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            DG.inspect(target, settle=0)
            self.assertEqual(DG.inspect(target, settle=0)["action"], "ok")

    def test_changed_plaintext_refreshes_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            DG.inspect(target, settle=0)
            (target / "draft_meta_info.json").write_text(
                json.dumps({"draft_name": "edited"}), encoding="utf-8")
            self.assertEqual(DG.inspect(target, settle=0)["action"], "snapshot")
            manifest = PG.read_manifest(target)
            names = [entry["name"] for entry in manifest["files"]]
            self.assertIn("draft_meta_info.json", names)

    def test_encrypted_draft_is_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            DG.inspect(target, settle=0)          # 先有快照
            encrypt_in_place(target)    # 剪映保存
            event = DG.inspect(target, settle=0)
            self.assertEqual(event["action"], "restore")
            self.assertTrue(PG.is_plaintext(target / "draft_info.json"))
            self.assertTrue(event["encryptedBackup"])

    def test_encrypted_without_main_snapshot_salvages_then_reports_partial(self):
        """主文件已密文、没有完整快照：先把残余明文抢救下来，之后只报一次 partial。"""
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            (target / "draft_content.json").write_text(
                json.dumps({"tracks": []}), encoding="utf-8")
            encrypt_in_place(target)
            first = DG.inspect(target, settle=0)
            self.assertEqual(first["action"], "salvaged")
            # 不能就地伪造一份明文主文件出来
            self.assertFalse(PG.is_plaintext(target / "draft_info.json"))
            self.assertTrue(PG.read_manifest(target)["partial"])
            self.assertEqual(DG.inspect(target, settle=0)["action"], "partial")
            self.assertEqual(DG.inspect(target, settle=0)["action"], "partial")

    def test_nothing_plain_at_all_is_unrecoverable(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "locked"
            target.mkdir()
            (target / "draft_info.json").write_bytes(ENCRYPTED)
            event = DG.inspect(target, settle=0)
            self.assertEqual(event["action"], "unrecoverable")

    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            DG.inspect(target, settle=0)
            encrypt_in_place(target)
            event = DG.inspect(target, dry_run=True, settle=0)
            self.assertEqual(event["action"], "would_restore")
            self.assertFalse(PG.is_plaintext(target / "draft_info.json"))

    def test_no_restore_flag_keeps_encrypted(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            DG.inspect(target, settle=0)
            encrypt_in_place(target)
            self.assertEqual(DG.inspect(target, restore=False, settle=0)["action"], "would_restore")
            self.assertFalse(PG.is_plaintext(target / "draft_info.json"))

    def test_missing_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = DG.inspect(Path(tmp) / "nope")
            self.assertEqual(event["action"], "missing")


class FreshEventsTest(unittest.TestCase):
    def test_only_state_changes_are_kept(self):
        events = [{"draft": "a", "action": "partial"}, {"draft": "b", "action": "restore"}]
        self.assertEqual(DG.fresh_events(events, {}), events)
        prev = {"a": "partial", "b": "restore"}
        self.assertEqual(DG.fresh_events(events, prev), [])
        self.assertEqual(
            DG.fresh_events([{"draft": "a", "action": "needs_review"}], prev),
            [{"draft": "a", "action": "needs_review"}])


class ScanTest(unittest.TestCase):
    def test_scan_skips_store_dir_and_selfcheck(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_draft(root, "studio_a__r1")
            make_draft(root, "selfcheck__bad")
            (root / ".studio-plain").mkdir()
            report = DG.scan(root, settle=0)
            self.assertEqual(report["scanned"], 1)
            self.assertEqual([e["draft"] for e in report["actions"]], ["studio_a__r1"])

    def test_scan_reports_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_draft(root, "a")
            make_draft(root, "b")
            report = DG.scan(root, settle=0)
            self.assertEqual(report["counts"], {"snapshot": 2})

    def test_journal_writes_one_line_per_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = Path(tmp) / "guard.jsonl"
            make_draft(root, "a")
            DG.run_once(root, journal_path=log, settle=0)
            DG.run_once(root, journal_path=log, settle=0)  # 第二轮无事，不写
            lines = log.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["action"], "snapshot")

    def test_error_in_one_draft_does_not_stop_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_draft(root, "good")
            broken = root / "broken"
            broken.mkdir()
            (broken / "draft_info.json").write_text("{not json", encoding="utf-8")
            report = DG.scan(root, settle=0)
            self.assertIn("good", [e["draft"] for e in report["actions"]])


if __name__ == "__main__":
    unittest.main()
