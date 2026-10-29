"""输入通道阶梯的离线用例：不发事件、不碰剪映。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

MW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MW))

import input_tiers as it  # noqa: E402


class Fake:
    def __init__(self, rcs=(0, 0, 0)):
        self.rcs = list(rcs)
        self.calls = []

    def __call__(self, cmd, timeout=30):
        self.calls.append(cmd)
        rc = self.rcs.pop(0) if self.rcs else 0
        return type("P", (), {"returncode": rc, "stdout": "", "stderr": ""})()


class ArgvTest(unittest.TestCase):
    def test_ax_argv(self):
        self.assertEqual(it.argv_ax("/x/ax_tool", "App", "ExportOkBtn", 2),
                         ["/x/ax_tool", "click", "App", "ExportOkBtn", "--via", "ax",
                          "--index", "2"])

    def test_pid_and_hid_argv_share_point(self):
        self.assertEqual(it.argv_pid("/x/ax_tool", -12, 1412), ["/x/ax_tool", "bgclick", "-12", "1412"])
        self.assertEqual(it.argv_hid("/x/ax_tool", -12, 1412, 2),
                         ["/x/ax_tool", "click-at", "-12", "1412", "--count", "2"])

    def test_ax_without_needle_raises(self):
        with self.assertRaises(ValueError):
            it.argv_for("ax", "/x/ax_tool")

    def test_unknown_tier_raises(self):
        with self.assertRaises(ValueError):
            it.argv_for("cdp", "/x/ax_tool", needle="x")


class LadderTest(unittest.TestCase):
    def test_stops_at_first_effective_tier(self):
        fake = Fake()
        out = it.ladder("/x", needle="Btn", verify=lambda: True, tiers=("ax", "pid", "hid"),
                        runner=fake)
        self.assertEqual(out["tier"], "ax")
        self.assertEqual(len(out["tried"]), 1)
        self.assertEqual(len(fake.calls), 1)

    def test_success_code_without_effect_falls_through(self):
        # 剪映现状：AXPress 返回 0 但界面不动 → 必须继续试下一级
        fake = Fake()
        seen = {"n": 0}

        def verify():
            seen["n"] += 1
            return seen["n"] >= 3          # 只有第 3 次（hid）之后才真的变了

        out = it.ladder("/x", needle="Btn", verify=verify, tiers=("ax", "pid", "hid"),
                        runner=Fake())
        self.assertEqual(out["tier"], "hid")
        self.assertEqual([t["tier"] for t in out["tried"]], ["ax", "pid", "hid"])
        self.assertEqual([t["effective"] for t in out["tried"]], [False, False, True])

    def test_nonzero_rc_skips_verify_and_falls_through(self):
        fake = Fake(rcs=(3, 0))
        out = it.ladder("/x", needle="Btn", verify=lambda: True, tiers=("ax", "pid"), runner=fake)
        self.assertEqual(out["tier"], "pid")
        self.assertEqual(out["tried"][0]["rc"], 3)

    def test_all_tiers_fail_reports_none(self):
        out = it.ladder("/x", needle="Btn", verify=lambda: False, tiers=("ax", "pid", "hid"),
                        runner=Fake())
        self.assertIsNone(out["tier"])
        self.assertFalse(out["effective"])
        self.assertEqual(len(out["tried"]), 3)

    def test_click_record_carries_argv_and_elapsed(self):
        rec = it.click("hid", "/x/ax_tool", runner=Fake(), x=10, y=20, pause=0)
        self.assertEqual(rec["argv"], ["click-at", "10", "20"])
        self.assertTrue(rec["ok"])
        self.assertIsNone(rec["verified"])      # 没给 verify 就不要假装验证过
        self.assertIn("elapsed_ms", rec)


class PolicyTest(unittest.TestCase):
    def test_borrow_requires_idle_gate_env(self):
        import os
        old = os.environ.pop(it.QUIET_ENV, None)
        try:
            self.assertFalse(it.borrow_allowed())
            os.environ[it.QUIET_ENV] = "12"
            self.assertTrue(it.borrow_allowed())
            os.environ[it.QUIET_ENV] = "0"
            self.assertFalse(it.borrow_allowed())
        finally:
            os.environ.pop(it.QUIET_ENV, None)
            if old is not None:
                os.environ[it.QUIET_ENV] = old

    def test_receipt_is_evidence_shaped(self):
        out = {"tier": "hid", "effective": True,
               "tried": [{"tier": "ax", "rc": 0, "verified": False, "elapsed_ms": 120},
                         {"tier": "hid", "rc": 0, "verified": True, "elapsed_ms": 90}]}
        r = it.receipt(out, note="导出面板取消")
        self.assertEqual(r["tier"], "hid")
        self.assertEqual([t["tier"] for t in r["tried"]], ["ax", "hid"])
        self.assertTrue(r["tried"][0]["verified"] is False)
        self.assertEqual(r["note"], "导出面板取消")


if __name__ == "__main__":
    unittest.main()
