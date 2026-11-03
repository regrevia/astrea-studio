"""`/guard` 接口的汇总逻辑：面板只该看到「每份草稿最后一句」和最近若干条事件。

流水账是守护进程写的，行可能坏、文件可能还没建；这两种情况都不能让接口 500。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capabilities_server as srv  # noqa: E402
from export import draft_guard as DG  # noqa: E402


class GuardSummaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.journal = self.tmp / "draft-guard.jsonl"
        self.old = DG.JOURNAL
        DG.JOURNAL = self.journal

    def tearDown(self):
        DG.JOURNAL = self.old

    def _write(self, rows: list[dict]):
        self.journal.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8")

    def test_last_action_per_draft_wins(self):
        self._write([
            {"draft": "a", "action": "snapshot", "at": "t1"},
            {"draft": "b", "action": "ok", "at": "t2"},
            {"draft": "a", "action": "restore", "at": "t3"},
        ])
        out = srv.guard_summary(limit=5)
        self.assertTrue(out["ok"])
        latest = {row["draft"]: row["action"] for row in out["drafts"]}
        self.assertEqual(latest, {"a": "restore", "b": "ok"})
        self.assertEqual([row["draft"] for row in out["events"]], ["a", "b", "a"])

    def test_limit_keeps_newest_events(self):
        self._write([{"draft": str(i), "action": "ok"} for i in range(10)])
        out = srv.guard_summary(limit=3)
        self.assertEqual([row["draft"] for row in out["events"]], ["7", "8", "9"])
        self.assertEqual(len(out["drafts"]), 10)

    def test_broken_line_is_skipped(self):
        self.journal.write_text(
            '{"draft": "a", "action": "ok"}\nnot json\n\n'
            '{"draft": "b", "action": "partial"}\n',
            encoding="utf-8")
        out = srv.guard_summary()
        self.assertTrue(out["ok"])
        self.assertEqual(sorted(row["draft"] for row in out["drafts"]), ["a", "b"])

    def test_missing_journal_is_not_an_error(self):
        out = srv.guard_summary()
        self.assertTrue(out["ok"])
        self.assertEqual(out["drafts"], [])
        self.assertEqual(out["events"], [])

    def test_limit_is_clamped(self):
        self._write([{"draft": str(i), "action": "ok"} for i in range(300)])
        self.assertEqual(len(srv.guard_summary(limit=0)["events"]), 1)
        self.assertEqual(len(srv.guard_summary(limit=999)["events"]), 200)


if __name__ == "__main__":
    unittest.main()
