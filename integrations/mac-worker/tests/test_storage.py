"""成片落点解析：默认走外置盘，盘不在就降级内置盘并说明原因，不能把任务搞挂。

五六条断言覆盖的就是这轮改动的边界：分辨率优先级、拔盘降级、路径创建、结果里的
落点字段。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import storage  # noqa: E402


class StorageResolveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.config = self.tmp / "config"
        self.config.mkdir()
        self.old = (storage.CONFIG_DIR, storage.STUDIO_HOME, os.environ.get("STUDIO_STORAGE_ROOT"),
                    os.environ.get("STUDIO_OUT"), os.environ.get("DELIVER_DIR"))
        storage.CONFIG_DIR = self.config
        storage.STUDIO_HOME = self.home
        for key in ("STUDIO_STORAGE_ROOT", "STUDIO_OUT", "DELIVER_DIR"):
            os.environ.pop(key, None)
        self.volume = self.tmp / "ext/CreatorStudio"
        os.environ["STUDIO_STORAGE_ROOT"] = str(self.volume)

    def tearDown(self):
        storage.CONFIG_DIR, storage.STUDIO_HOME = self.old[0], self.old[1]
        for key, value in zip(("STUDIO_STORAGE_ROOT", "STUDIO_OUT", "DELIVER_DIR"), self.old[2:]):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_defaults_point_at_storage_root(self):
        got = storage.resolve()
        self.assertIsNone(got.fallback_reason)
        self.assertEqual(got.out, self.volume / "out")
        self.assertEqual(got.deliver, self.volume / "deliver")
        self.assertEqual(got.work, self.volume / "work")
        # 草稿留在内置盘：剪映互操作与加密守护都按内部路径写的
        self.assertEqual(got.drafts, self.home / "jianying")

    def test_missing_volume_falls_back_internal(self):
        os.environ["STUDIO_STORAGE_ROOT"] = "/Volumes/__no_such_disk_here__/CreatorStudio"
        got = storage.resolve()
        self.assertTrue(got.fallback_reason)
        self.assertIn("未挂载", got.fallback_reason)
        self.assertEqual(got.out, self.home / "out")
        self.assertEqual(got.deliver, self.home / "deliver")
        self.assertEqual(got.describe()["fallbackReason"], got.fallback_reason)

    def test_env_override_beats_root_and_config(self):
        os.environ["STUDIO_OUT"] = str(self.tmp / "claims")
        (self.config / "studio.json").write_text(
            '{"storage": {"root": "%s", "deliver": "%s"}}' % (self.volume, self.tmp / "cfg-deliver"),
            encoding="utf-8")
        got = storage.resolve()
        self.assertEqual(got.out, self.tmp / "claims")
        self.assertEqual(got.deliver, self.tmp / "cfg-deliver")

    def test_work_dir_is_created(self):
        path = storage.resolve().work_dir("timeline", "job-1")
        self.assertTrue(path.is_dir())
        self.assertEqual(path.parent.name, "timeline")

    def test_jobs_follow_storage(self):
        import jobs
        os.environ["STUDIO_STORAGE_ROOT"] = str(self.volume)
        self.assertEqual(jobs.deliver_dir(), self.volume / "deliver")
        self.assertEqual(jobs.claim_dir(), self.volume / "out")
        self.assertIn("root", jobs.storage_info())


if __name__ == "__main__":
    unittest.main()
