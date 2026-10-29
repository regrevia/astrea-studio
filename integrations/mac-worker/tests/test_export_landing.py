"""成片入库收口（体检证据 → library.land → 失败留痕不丢文件）的离线用例。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

MW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MW))

import export_landing as landing  # noqa: E402


PAYLOAD = {
    "libraryProjectName": "studio", "libraryProject": "proj-1",
    "libraryTitle": "首图测试", "platform": "douyin", "runId": "run-9",
    "draft": "studio_x__r1", "draftPath": "/drafts/studio_x__r1",
}


class PayloadTest(unittest.TestCase):
    def test_explicit_library_fields_win(self):
        fields = landing.land_payload(PAYLOAD, "job1")
        self.assertEqual(fields["project"], "studio")
        self.assertEqual(fields["project_id"], "proj-1")
        self.assertEqual(fields["title"], "首图测试")
        self.assertEqual(fields["platform"], "douyin")
        self.assertEqual(fields["run_id"], "run-9")

    def test_falls_back_to_run_id_then_job_id(self):
        self.assertEqual(landing.land_payload({"runId": "run-9"}, "job1")["title"], "run-9")
        self.assertEqual(landing.land_payload({}, "job1")["title"], "job1")
        self.assertEqual(landing.land_payload({}, "job1")["platform"], "douyin")
        self.assertEqual(landing.land_payload({}, "job1")["project"], "studio")


class EvidenceTest(unittest.TestCase):
    def _write(self, root: Path, job: str, rec: dict) -> None:
        d = root / landing.EVIDENCE_REL / job
        d.mkdir(parents=True, exist_ok=True)
        (d / "r1__douyin.json").write_text(json.dumps(rec), encoding="utf-8")

    def test_reads_checks_profile_panel_and_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            self._write(root, "job1", {"probe": {"duration": 11.2},
                                       "checks": {"duration": {"ok": True}},
                                       "profile": {"tier": "1080P"}, "panel": "剪映"})
            with tempfile.TemporaryDirectory() as out:
                meta = landing.read_evidence("job1", out, roots=[root])
            self.assertEqual(meta["panel"], "剪映")
            self.assertEqual(meta["checks"]["duration"], {"ok": True})

    def test_first_root_with_evidence_wins_and_broken_json_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty, good = Path(tmp) / "a", Path(tmp) / "b"
            self._write(good, "job1", {"probe": {"duration": 5.0}})
            with tempfile.TemporaryDirectory() as out:
                self.assertEqual(landing.read_evidence("job1", out, roots=[empty, good])
                                 ["probe"]["duration"], 5.0)
            d = good / landing.EVIDENCE_REL / "job2"
            d.mkdir(parents=True, exist_ok=True)
            (d / "r1__douyin.json").write_text("{broken", encoding="utf-8")
            with tempfile.TemporaryDirectory() as out:
                self.assertEqual(landing.read_evidence("job2", out, roots=[good]), {})

    def test_no_evidence_is_empty(self):
        with tempfile.TemporaryDirectory() as out:
            self.assertEqual(landing.read_evidence("nope", out, roots=[]), {})


class LandTest(unittest.TestCase):
    def test_passes_evidence_into_meta_and_returns_row(self):
        calls = {}

        def fake_land(video, **kw):
            calls.update(kw)
            calls["video"] = video
            return {"path": "/lib/a.mp4", "assetId": "a1", "md5": "m"}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            d = root / landing.EVIDENCE_REL / "job1"
            d.mkdir(parents=True)
            (d / "r1__douyin.json").write_text(
                json.dumps({"probe": {"duration": 6.0}, "profile": {"tier": "720P"},
                            "checks": {"fps": {"ok": True}}, "panel": "panel"}),
                encoding="utf-8")
            row = landing.land("/out/job1/r1/a.mp4", PAYLOAD, "job1", 1, str(Path(tmp) / "out"),
                               roots=[root], land_fn=fake_land)
        self.assertEqual(row["assetId"], "a1")
        self.assertEqual(calls["duration"], 6.0)
        self.assertEqual(calls["meta"]["profile"], {"tier": "720P"})
        self.assertEqual(calls["meta"]["panel"], "panel")
        self.assertEqual(calls["revision"], 1)
        self.assertEqual(calls["job_id"], "job1")
        self.assertEqual(calls["draft_path"], "/drafts/studio_x__r1")

    def test_failure_returns_none_and_warns_without_losing_file(self):
        warns = []

        def boom(*_a, **_kw):
            raise FileExistsError("这一版已经有成片了")

        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "keep.mp4"
            video.write_bytes(b"v")
            row = landing.land(str(video), PAYLOAD, "job1", 1, tmp, roots=[],
                               land_fn=boom, warn=lambda *a: warns.append(a))
            self.assertIsNone(row)
            self.assertTrue(video.is_file(), "入库失败把成片弄丢了")
            self.assertEqual(len(warns), 1)
            self.assertIn("仍留在", warns[0][0])

    def test_real_library_duplicate_revision_is_rejected(self):
        import library

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "clip.mp4"
            src.write_bytes(b"video-bytes")
            lib_root = Path(tmp) / "lib"
            row1 = landing.land(str(src), PAYLOAD, "job1", 1, tmp,
                                land_fn=lambda p, **kw: library.land(p, root=lib_root, **kw))
            self.assertIsNotNone(row1)
            self.assertIn("__douyin__r1", row1["path"])
            again = Path(tmp) / "clip2.mp4"
            again.write_bytes(b"other")
            row2 = landing.land(str(again), PAYLOAD, "job1", 1, tmp,
                                land_fn=lambda p, **kw: library.land(p, root=lib_root, **kw),
                                warn=lambda *a: None)
            # 库的规则是「不覆盖」而不是「拒绝」：第二次落成新名字，旧成片原样保留
            self.assertIsNotNone(row2)
            self.assertNotEqual(row2["path"], row1["path"])
            self.assertTrue(Path(row1["path"]).is_file(), "二次入库把上一版覆盖了")


if __name__ == "__main__":
    unittest.main()
