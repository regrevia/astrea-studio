"""剪映版本适配层的单测：检测、参考草稿优先、盖章不误伤。"""
from __future__ import annotations

import json
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

MW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MW))

import jianying_versions as V  # noqa: E402


def _fake_app(root: Path, version: str = "11.6.0", build: str = "490") -> Path:
    app = root / "VideoFusion-macOS.app"
    contents = app / "Contents"
    contents.mkdir(parents=True)
    with (contents / "Info.plist").open("wb") as fh:
        plistlib.dump({
            "CFBundleIdentifier": "com.lemon.lvpro",
            "CFBundleShortVersionString": version,
            "CFBundleVersion": build,
        }, fh)
    return app


def _fake_draft(root: Path, name: str, version: str, app_version: str,
                new_version: str = "110.0.0", os_name: str = "mac",
                mtime: float = 0.0) -> Path:
    draft = root / name
    draft.mkdir(parents=True)
    (draft / "draft_content.json").write_text(json.dumps({
        "version": version,
        "new_version": new_version,
        "platform": {"app_id": 3704, "app_source": "lv",
                     "app_version": app_version, "os": os_name},
        "last_modified_platform": {"app_version": app_version, "os": os_name},
    }), encoding="utf-8")
    if mtime:
        import os
        os.utime(draft, (mtime, mtime))
    return draft


class ReadAppInfoTest(unittest.TestCase):
    def test_missing_bundle_is_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = V.read_app_info(Path(tmp) / "nope.app")
        self.assertFalse(info["exists"])

    def test_reads_version_and_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = _fake_app(Path(tmp), "12.3.1", "777")
            info = V.read_app_info(app)
        self.assertTrue(info["exists"])
        self.assertEqual(info["version"], "12.3.1")
        self.assertEqual(info["build"], "777")
        self.assertEqual(info["bundleId"], "com.lemon.lvpro")

    def test_broken_plist_is_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Broken.app"
            contents = app / "Contents"
            contents.mkdir(parents=True)
            (contents / "Info.plist").write_text("not a plist", encoding="utf-8")
            info = V.read_app_info(app)
        self.assertFalse(info["exists"])
        self.assertIn("error", info)


class ResolveSchemaTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.drafts = self.root / "drafts"
        self.drafts.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_prefers_reference_from_same_major(self):
        _fake_draft(self.drafts, "old", 360000, "10.2.0", mtime=100)
        _fake_draft(self.drafts, "new", 360000, "11.6.0", new_version="116.0.0", mtime=200)
        schema = V.resolve_schema(app_version="11.6.0", app_build="490",
                                  draft_root=self.drafts)
        self.assertEqual(schema["source"], "reference-same-major")
        self.assertEqual(schema["referenceDraft"], "new")
        self.assertEqual(schema["new_version"], "116.0.0")
        # 参考草稿来自另一台机器（或旧安装），落到本机要改成本机版本
        self.assertEqual(schema["platform"]["app_version"], "11.6.0")
        self.assertEqual(schema["platform"]["app_build"], "490")
        self.assertEqual(schema["last_modified_platform"]["app_version"], "11.6.0")

    def test_other_major_reference_is_not_adopted(self):
        _fake_draft(self.drafts, "only", 360000, "10.2.0", mtime=50)
        schema = V.resolve_schema(app_version="12.0.0", draft_root=self.drafts)
        self.assertEqual(schema["source"], "keep-template")
        self.assertFalse(schema["apply"])

    def test_keeps_template_when_no_evidence(self):
        # 没装同主版本草稿时不给版本块盖章，沿用模板（11.6 实测可开）
        schema = V.resolve_schema(app_version="12.0.0", draft_root=self.drafts)
        self.assertEqual(schema["source"], "keep-template")
        self.assertFalse(schema["apply"])
        self.assertIsNone(schema["version"])

    def test_scan_skips_broken_and_old_format_drafts(self):
        _fake_draft(self.drafts, "good", 360000, "11.0.0")
        broken = self.drafts / "broken"
        broken.mkdir()
        (broken / "draft_content.json").write_text("GGHbtvxhfjg 密文", encoding="utf-8")
        empty = self.drafts / "no-platform"
        empty.mkdir()
        (empty / "draft_content.json").write_text("{\"version\": 1}", encoding="utf-8")
        refs = V.scan_reference_drafts(self.drafts)
        self.assertEqual([r["draft"] for r in refs], ["good"])

    def test_missing_draft_root_is_empty(self):
        self.assertEqual(V.scan_reference_drafts(self.root / "nope"), [])

    def test_describe_reports_counts(self):
        _fake_draft(self.drafts, "a", 360000, "11.6.0")
        _fake_draft(self.drafts, "b", 360000, "10.2.0")
        info = V.describe(app_path=self.root / "missing.app", draft_root=self.drafts)
        self.assertEqual(info["references"], 2)
        self.assertEqual(info["referenceApps"], ["10.2.0", "11.6.0"])
        self.assertFalse(info["app"]["exists"])


class StampTest(unittest.TestCase):
    def test_stamp_writes_version_blocks_and_marks_mac(self):
        content = {
            "id": "ABC",
            "version": 360000,
            "new_version": "110.0.0",
            "platform": {"app_id": 3704, "app_source": "lv",
                         "app_version": "10.2.0", "os": "windows", "device_id": "d1"},
            "last_modified_platform": {"app_version": "10.2.0", "os": "windows"},
        }
        schema = {"version": 360000, "new_version": "116.0.0",
                  "platform": {"app_id": 3704, "app_source": "lv",
                               "app_version": "11.6.0", "os": "mac"},
                  "last_modified_platform": {"app_version": "11.6.0", "os": "mac"},
                  "source": "reference-same-major", "referenceDraft": "new"}
        info = V.stamp(content, schema, os_name="mac")
        self.assertEqual(content["version"], 360000)
        self.assertEqual(content["new_version"], "116.0.0")
        self.assertEqual(content["platform"]["app_version"], "11.6.0")
        self.assertEqual(content["platform"]["os"], "mac")
        # 模板里其它字段不能被冲掉
        self.assertEqual(content["platform"]["device_id"], "d1")
        self.assertEqual(content["last_modified_platform"]["app_version"], "11.6.0")
        self.assertEqual(info["before"]["appVersion"], "10.2.0")
        self.assertEqual(info["after"]["appVersion"], "11.6.0")
        self.assertEqual(info["source"], "reference-same-major")

    def test_stamp_is_idempotent(self):
        content = {"platform": {}, "last_modified_platform": {}}
        schema = {"version": 360000, "new_version": "110.0.0",
                  "platform": {"app_id": 3704, "app_version": "11.6.0", "os": "mac"},
                  "last_modified_platform": {"app_version": "11.6.0", "os": "mac"},
                  "source": "builtin"}
        V.stamp(content, schema, os_name="mac")
        first = json.dumps(content, sort_keys=True)
        V.stamp(content, schema, os_name="mac")
        self.assertEqual(json.dumps(content, sort_keys=True), first)


if __name__ == "__main__":
    unittest.main()
