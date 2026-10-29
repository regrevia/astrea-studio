"""version_evidence 的离线用例：只碰临时目录，不读真机草稿。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import version_evidence as VE  # noqa: E402


def make_draft(root: Path, name: str, *, marker: bool = True,
               encrypted: bool = False, sidecar: dict | None = None) -> Path:
    d = root / name
    d.mkdir(parents=True)
    if encrypted:
        (d / "draft_info.json").write_bytes(b"GA2Jp0QJD1f0ItqASwyz7g52jynj1S+w9ae70RdaHcBEzw/h")
        (d / "draft_meta_info.json").write_bytes(b"i\x00\x00\x00i\x00\x00\x00ei")
    else:
        content = {"version": 360000, "new_version": "110.0.0",
                   "platform": {"app_version": "10.2.0", "os": "windows"},
                   "tracks": [], "materials": {}}
        (d / "draft_info.json").write_text(json.dumps(content), encoding="utf-8")
        (d / "draft_content.json").write_text(json.dumps(content), encoding="utf-8")
    if marker:
        (d / "studio_export_meta.json").write_text(
            json.dumps(sidecar or {"project": name, "engine": "jianying"}), encoding="utf-8")
    return d


class ClassifyTest(unittest.TestCase):
    def test_plaintext(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_draft(Path(tmp), "a")
            self.assertEqual(VE.classify(d), "plaintext")

    def test_native_when_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_draft(Path(tmp), "a", encrypted=True)
            self.assertEqual(VE.classify(d), "native")

    def test_native_when_json_broken(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "x"
            d.mkdir()
            (d / "draft_info.json").write_text("{not json", encoding="utf-8")
            self.assertEqual(VE.classify(d), "native")

    def test_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "x"
            d.mkdir()
            self.assertEqual(VE.classify(d), "missing")


class ObserveTest(unittest.TestCase):
    def test_encrypted_engine_draft_is_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_draft(Path(tmp), "studio__r1", encrypted=True)
            e = VE.observe(d, app_path=Path(tmp) / "nope.app")
            self.assertEqual(e["verdict"], "accepted_by_app")
            self.assertIn("回写", e["reason"])
            self.assertEqual(e["after"]["kind"], "native")
            self.assertTrue(e["after"]["files"]["draft_info.json"]["size"] > 0)

    def test_plaintext_is_not_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_draft(Path(tmp), "studio__r2")
            e = VE.observe(d, app_path=Path(tmp) / "nope.app")
            self.assertEqual(e["verdict"], "no_evidence")
            self.assertIn("还没打开", e["reason"])

    def test_foreign_draft_is_not_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_draft(Path(tmp), "someone_else", marker=False, encrypted=True)
            e = VE.observe(d, app_path=Path(tmp) / "nope.app")
            self.assertEqual(e["verdict"], "no_evidence")
            self.assertIn("不是引擎写", e["reason"])


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dir = self.root / "evidence"
        self.dir.mkdir()
        self._old = (VE.EVIDENCE_DIR, VE.LEDGER)
        VE.EVIDENCE_DIR = self.dir
        VE.LEDGER = self.dir / "ledger.jsonl"

    def tearDown(self):
        VE.EVIDENCE_DIR, VE.LEDGER = self._old
        self.tmp.cleanup()

    def _write_app(self, version="11.6.0", build="490"):
        import plistlib

        app = self.root / "VideoFusion-macOS.app"
        (app / "Contents").mkdir(parents=True, exist_ok=True)
        with (app / "Contents/Info.plist").open("wb") as fh:
            plistlib.dump({"CFBundleShortVersionString": version,
                           "CFBundleVersion": build}, fh)
        return app

    def test_record_writes_file_and_ledger(self):
        app = self._write_app()
        d = make_draft(self.root, "studio__r1", encrypted=True)
        e = VE.record(d, app_path=app)
        self.assertEqual(e["verdict"], "accepted_by_app")
        self.assertEqual(e["app"]["version"], "11.6.0")
        self.assertTrue((VE.EVIDENCE_DIR / f"{e['draft']}__{e['id']}.json").is_file())
        self.assertEqual(len(VE.read_ledger()), 1)

    def test_record_is_idempotent_per_version(self):
        app = self._write_app()
        d = make_draft(self.root, "studio__r1", encrypted=True)
        first = VE.record(d, app_path=app)
        second = VE.record(d, app_path=app)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(VE.read_ledger()), 1)

    def test_record_skips_non_evidence(self):
        d = make_draft(self.root, "studio__r1")
        e = VE.record(d, app_path=self.root / "nope.app")
        self.assertEqual(e["verdict"], "no_evidence")
        self.assertEqual(VE.read_ledger(), [])

    def test_status_filters_by_major(self):
        app = self._write_app()
        VE.record(make_draft(self.root, "a", encrypted=True), app_path=app)
        rep = VE.status_report("11.6.0")
        self.assertEqual(rep["status"], "accepted")
        self.assertEqual(rep["count"], 1)
        self.assertEqual(VE.status_report("12.0.0")["status"], "none")
        self.assertEqual(VE.status_report("11.0.0")["count"], 1)

    def test_scan_finds_new_and_skips_known(self):
        app = self._write_app()
        root = self.root / "drafts"
        root.mkdir()
        make_draft(root, "studio__r1", encrypted=True)
        make_draft(root, "studio__r2", encrypted=True)
        make_draft(root, "studio__r3")  # 明文，不算
        fresh = VE.scan(root, app_path=app)
        self.assertEqual(sorted(e["draft"] for e in fresh), ["studio__r1", "studio__r2"])
        self.assertEqual(VE.scan(root, app_path=app), [])
        self.assertEqual(len(VE.read_ledger()), 2)


class SupportReportTest(unittest.TestCase):
    """support_report 在没有参考草稿时，用行为实证把 template 升级成 accepted。"""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import jianying_versions as JV

        self.JV = JV
        self.schema = {"source": "keep-template", "apply": False,
                       "app": {"version": "11.6.0", "build": "490"}}

    def test_template_without_evidence(self):
        rep = self.JV.support_report(self.schema)
        self.assertEqual(rep["status"], "template")

    def test_accepted_with_same_major_evidence(self):
        ev = {"status": "accepted", "major": 11, "count": 2, "note": "剪映 11.6.0 接受过"}
        rep = self.JV.support_report(self.schema, evidence=ev)
        self.assertEqual(rep["status"], "accepted")
        self.assertEqual(rep["note"], "剪映 11.6.0 接受过")
        self.assertEqual(rep["evidence"]["count"], 2)

    def test_evidence_other_major_does_not_upgrade(self):
        ev = {"status": "accepted", "major": 10, "count": 1, "note": "x"}
        rep = self.JV.support_report(self.schema, evidence=ev)
        self.assertEqual(rep["status"], "template")

    def test_reference_same_major_wins_over_evidence(self):
        schema = dict(self.schema, source="reference-same-major", apply=True)
        ev = {"status": "accepted", "major": 11, "count": 2, "note": "x"}
        rep = self.JV.support_report(schema, evidence=ev)
        self.assertEqual(rep["status"], "adopted")


if __name__ == "__main__":
    unittest.main()
