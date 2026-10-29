"""plain_guard 的离线用例：临时目录里造明文/密文草稿，不碰真实草稿、不启动剪映。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "export"))

import plain_guard as PG  # noqa: E402

# 剪映回写后的真实开头（base64 密文），不需要能解开，只要不是 JSON 就够
ENCRYPTED = b"GA2Jp0QhZ2VuZXJhdGVkLWJ5LWppYW55aW5nLWVuY3J5cHRlZA=="


def make_draft(tmp: Path, name: str = "studio_a__r1") -> Path:
    target = tmp / name
    target.mkdir()
    (target / "draft_info.json").write_text(
        json.dumps({"canvases": [{"width": 1080, "height": 1920}]}), encoding="utf-8")
    (target / "draft_meta_info.json").write_text(
        json.dumps({"draft_name": name, "draft_fold_path": str(target)}), encoding="utf-8")
    (target / "studio_export_meta.json").write_text(
        json.dumps({"fps": 30, "duration_us": 11234000}), encoding="utf-8")
    return target


def encrypt_in_place(target: Path) -> None:
    """模拟剪映打开并保存：两个主文件变成密文，行为证据文件保留。"""
    (target / "draft_info.json").write_bytes(ENCRYPTED)
    (target / "draft_meta_info.json").write_bytes(ENCRYPTED)


class PlaintextTest(unittest.TestCase):
    def test_json_is_plaintext(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            self.assertTrue(PG.is_plaintext(target / "draft_info.json"))

    def test_base64_is_not_plaintext(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            encrypt_in_place(target)
            self.assertFalse(PG.is_plaintext(target / "draft_info.json"))

    def test_canonical_lists_only_readable_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            encrypt_in_place(target)
            self.assertEqual([p.name for p in PG.canonical(target)], [])
            self.assertEqual(
                sorted(p.name for p in PG.encrypted(target)),
                ["draft_info.json", "draft_meta_info.json"])

    def test_draft_dir_resolves_name_under_default_root(self):
        self.assertEqual(PG.draft_dir("studio_x__r1"), PG.DRAFT_ROOT / "studio_x__r1")
        self.assertEqual(PG.draft_dir("/tmp/abs"), Path("/tmp/abs"))


class SnapshotTest(unittest.TestCase):
    def test_snapshot_copies_plain_files_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            manifest = PG.snapshot(target)
            self.assertEqual(manifest["draft"], target.name)
            names = [f["name"] for f in manifest["files"]]
            self.assertIn("draft_info.json", names)
            self.assertIn("studio_export_meta.json", names)  # 行为证据也要存
            store = PG.store_dir(target)
            self.assertTrue((store / "files/draft_info.json").is_file())
            self.assertTrue((store / "manifest.json").is_file())
            self.assertEqual(len(manifest["files"][0]["sha256"]), 64)

    def test_snapshot_keeps_store_outside_draft_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            PG.snapshot(target)
            self.assertFalse((target / "manifest.json").exists())
            self.assertIn(PG.STORE_DIRNAME, str(PG.store_dir(target)))

    def test_snapshot_refuses_encrypted_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            encrypt_in_place(target)
            with self.assertRaises(ValueError):
                PG.snapshot(target)

    def test_snapshot_does_not_clobber_with_encrypted_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            first = PG.snapshot(target)
            encrypt_in_place(target)
            with self.assertRaises(ValueError):
                PG.snapshot(target)
            self.assertEqual(PG.read_manifest(target)["createdAt"], first["createdAt"])

    def test_status_reports_restorable(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            PG.snapshot(target)
            encrypt_in_place(target)
            state = PG.status(target)
            self.assertTrue(state["hasSnapshot"])
            self.assertTrue(state["restorable"])
            self.assertEqual(sorted(state["encrypted"]), ["draft_info.json", "draft_meta_info.json"])


class RestoreTest(unittest.TestCase):
    def test_restore_brings_plaintext_back_and_backs_up_encrypted(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            PG.snapshot(target)
            encrypt_in_place(target)
            result = PG.restore(target)
            self.assertEqual(sorted(result["restored"]), ["draft_info.json", "draft_meta_info.json"])  # 引擎标记不写回
            self.assertEqual(sorted(result["backedUp"]), ["draft_info.json", "draft_meta_info.json"])
            self.assertTrue(PG.is_plaintext(target / "draft_info.json"))
            backup = Path(result["encryptedBackup"])
            self.assertEqual((backup / "draft_info.json").read_bytes(), ENCRYPTED)

    def test_restore_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            PG.snapshot(target)
            encrypt_in_place(target)
            result = PG.restore(target, dry_run=True)
            self.assertTrue(result["dryRun"])
            # dry-run 只报「会备份哪些」，不落盘
            self.assertEqual(sorted(result["backedUp"]), ["draft_info.json", "draft_meta_info.json"])
            self.assertEqual(result["encryptedBackup"], "")
            self.assertFalse(PG.is_plaintext(target / "draft_info.json"))
            self.assertFalse((PG.store_dir(target) / PG.ENC_BACKUP_DIRNAME).exists())

    def test_restore_without_snapshot_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            encrypt_in_place(target)
            with self.assertRaises(FileNotFoundError):
                PG.restore(target)

    def test_restore_is_repeatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            PG.snapshot(target)
            encrypt_in_place(target)
            PG.restore(target)
            again = PG.restore(target)
            self.assertEqual(again["backedUp"], [])  # 已经是明文，不用再备份


class EnsurePlainTest(unittest.TestCase):
    def test_plain_draft_gets_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            result = PG.ensure_plain(target)
            self.assertEqual(result["action"], "snapshot")
            self.assertTrue(PG.read_manifest(target))

    def test_encrypted_draft_gets_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            PG.snapshot(target)
            encrypt_in_place(target)
            result = PG.ensure_plain(target)
            self.assertEqual(result["action"], "restore")
            self.assertTrue(PG.is_plaintext(target / "draft_info.json"))

    def test_partial_rewrite_still_snapshots_and_records_gap(self):
        """剪映只改写了 draft_meta_info.json 时，主文件仍是明文 → 可存快照，缺口记在清单里。"""
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            (target / "draft_meta_info.json").write_bytes(ENCRYPTED)
            result = PG.ensure_plain(target)
            self.assertEqual(result["action"], "snapshot")
            self.assertEqual(result["detail"]["encryptedAtSnapshot"], ["draft_meta_info.json"])
            restored = PG.restore(target)
            self.assertEqual(restored["restored"], ["draft_info.json"])  # meta 没有明文快照，补不回来

    def test_no_plaintext_and_no_snapshot_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = make_draft(Path(tmp))
            encrypt_in_place(target)
            (target / "draft_content.json").unlink(missing_ok=True)
            with self.assertRaises(ValueError):
                PG.ensure_plain(target)


class ListStoresTest(unittest.TestCase):
    def test_list_reports_needs_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = make_draft(root)
            PG.snapshot(target)
            encrypt_in_place(target)
            rows = PG.list_stores(root)
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["needsRestore"])
            self.assertTrue(rows[0]["draftExists"])


if __name__ == "__main__":
    unittest.main()
