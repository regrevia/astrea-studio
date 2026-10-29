"""一键自检汇总器的单测：探针结果怎么压成一行、什么时候算失败。

不跑真机、不连库：只喂合成的探针 JSON。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

MW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MW / "tools"))

import check_all as C  # noqa: E402


def _case(name: str, verdict: str = "PASS", seconds: float = 2.8, checks: dict | None = None) -> dict:
    return {"case": name, "verdict": verdict, "elapsedSeconds": seconds,
            "checks": checks or {"ok": True}}


class SummarizeLaneTest(unittest.TestCase):
    def test_all_pass_reports_cases_and_timings(self):
        ok, detail = C.summarize_lane([
            _case("claim"), _case("cancelled-run", seconds=2.9),
            _case("stale-revision", seconds=0.5), _case("lease-expired", seconds=36.0),
        ])
        self.assertTrue(ok)
        self.assertIn("4 条全 PASS", detail)
        self.assertIn("lease-expired", detail)
        self.assertIn("2.9/0.5/36.0", detail)

    def test_first_failed_case_is_named_with_failed_assertions(self):
        ok, detail = C.summarize_lane([
            _case("claim"),
            _case("lease-expired", verdict="FAIL",
                  checks={"expired_failed": True, "attempt_marked": False}),
        ])
        self.assertFalse(ok)
        self.assertIn("lease-expired FAIL", detail)
        self.assertIn("attempt_marked", detail)
        self.assertNotIn("expired_failed", detail)

    def test_failure_without_checks_still_explains_itself(self):
        ok, detail = C.summarize_lane([{"case": "claim", "verdict": "FAIL"}])
        self.assertFalse(ok)
        self.assertIn("无断言输出", detail)

    def test_empty_output_is_failure_not_silence(self):
        ok, detail = C.summarize_lane([])
        self.assertFalse(ok)
        self.assertIn("没有返回任何结果", detail)


if __name__ == "__main__":
    unittest.main()
