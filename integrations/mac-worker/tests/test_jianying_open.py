"""jianying_open 的分支必须自己兜住：它会把剪映带到前台。

这里只测 handler 的判断，不启剪映：
- 缺 draftPath / 目录不存在 → 直接拒，不猜最新草稿；
- 屏幕锁着 → blocked/screen_locked，不跑 open_draft.py；
- rc=9 / rc=2 → 退避成 export_user_busy / export_draft_not_open；
- 成功 → stage=draft_opened，output.opened=True，且命令行里带的是草稿目录名。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import worker  # noqa: E402


def _progress(*_args: object) -> None:
    pass


class JianyingOpenTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.draft = Path(self._tmp.name) / "demo__r1"
        self.draft.mkdir()
        self.worker = object.__new__(worker.Worker)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _call(self, payload: dict) -> dict:
        return self.worker.do_jianying_open("job-1", payload, _progress)

    def test_missing_draft_path_rejected(self) -> None:
        with self.assertRaises(worker.JianyingError) as ctx:
            self._call({})
        self.assertIn("draftPath", str(ctx.exception))

    def test_missing_draft_dir_is_not_found(self) -> None:
        with self.assertRaises(worker.JianyingError) as ctx:
            self._call({"draftPath": str(self.draft / "nope")})
        self.assertIn("draft_not_found", str(ctx.exception))

    def test_screen_locked_does_not_launch(self) -> None:
        with mock.patch.object(worker, "screen_locked", return_value=True), \
                mock.patch.object(worker.subprocess, "run") as run:
            result = self._call({"draftPath": str(self.draft)})
        run.assert_not_called()
        self.assertEqual(result["stage"], "blocked")
        self.assertEqual(result["reasonCode"], "screen_locked")
        self.assertFalse(result["output"]["opened"])

    def test_busy_rc_backs_off(self) -> None:
        done = subprocess.CompletedProcess([], 9, stdout="", stderr="user busy")
        with mock.patch.object(worker, "screen_locked", return_value=False), \
                mock.patch.object(worker.subprocess, "run", return_value=done):
            result = self._call({"draftPath": str(self.draft)})
        self.assertEqual(result["stage"], "blocked")
        self.assertEqual(result["reasonCode"], "export_user_busy")
        self.assertFalse(result["output"]["opened"])

    def test_draft_not_open_rc2_is_blocked(self) -> None:
        done = subprocess.CompletedProcess([], 2, stdout="", stderr="no draft")
        with mock.patch.object(worker, "screen_locked", return_value=False), \
                mock.patch.object(worker.subprocess, "run", return_value=done):
            result = self._call({"draftPath": str(self.draft)})
        self.assertEqual(result["reasonCode"], "export_draft_not_open")

    def test_success_passes_draft_name_and_wait_idle(self) -> None:
        done = subprocess.CompletedProcess([], 0, stdout="opened", stderr="")
        with mock.patch.object(worker, "screen_locked", return_value=False), \
                mock.patch.object(worker.subprocess, "run", return_value=done) as run:
            result = self._call({"draftPath": str(self.draft), "waitIdle": 12})
        argv = run.call_args[0][0]
        self.assertIn("--name", argv)
        self.assertEqual(argv[argv.index("--name") + 1], self.draft.name)
        self.assertEqual(argv[argv.index("--wait-idle") + 1], "12.0")
        self.assertEqual(result["stage"], "draft_opened")
        self.assertTrue(result["output"]["opened"])
        self.assertEqual(result["draftName"], self.draft.name)

    def test_hard_failure_raises_with_reason(self) -> None:
        done = subprocess.CompletedProcess([], 7, stdout="", stderr="boom")
        with mock.patch.object(worker, "screen_locked", return_value=False), \
                mock.patch.object(worker.subprocess, "run", return_value=done):
            with self.assertRaises(worker.JianyingError) as ctx:
                self._call({"draftPath": str(self.draft)})
        self.assertIn("jianying_open_failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
