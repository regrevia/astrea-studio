import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import publish_sau as P


def _video(td: Path) -> Path:
    v = td / "clip.mp4"
    v.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return v


class PublishSpecTest(unittest.TestCase):
    def test_video_payload_takes_the_file_and_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            v = _video(Path(td))
            spec = P.PublishSpec.from_payload({
                "accountRef": "acct", "videoPath": str(v),
                "content": {"title": "标题", "desc": "描述", "tags": ["#a", "b"],
                            "thumbnailPath": str(v)},
            })
            self.assertEqual(spec.kind, "video")
            self.assertEqual(spec.video, v)
            self.assertEqual(spec.tags, ["a", "b"])
            self.assertEqual(spec.thumbnail, v)

    def test_missing_account_or_title_is_rejected_before_any_browser(self):
        with tempfile.TemporaryDirectory() as td:
            v = _video(Path(td))
            with self.assertRaises(P.PublishError):
                P.PublishSpec.from_payload({"videoPath": str(v),
                                            "content": {"title": "t"}})
            with self.assertRaises(P.PublishError):
                P.PublishSpec.from_payload({"accountRef": "a",
                                            "videoPath": str(v), "content": {}})

    def test_absent_video_file_is_rejected(self):
        with self.assertRaises(P.PublishError):
            P.PublishSpec.from_payload({
                "accountRef": "a", "videoPath": "/definitely/not/here.mp4",
                "content": {"title": "t"},
            })

    def test_note_payload_uses_image_list_and_rejects_missing_images(self):
        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "a.png"
            img.write_bytes(b"png")
            spec = P.PublishSpec.from_payload({
                "accountRef": "a",
                "content": {"type": "note", "title": "t", "imagePaths": [str(img)]},
            })
            self.assertEqual(spec.kind, "note")
            self.assertEqual(spec.images, [img])
            with self.assertRaises(P.PublishError):
                P.PublishSpec.from_payload({
                    "accountRef": "a",
                    "content": {"type": "note", "title": "t",
                                "imagePaths": [str(Path(td) / "nope.png")]},
                })

    def test_unknown_publication_type_is_rejected(self):
        with self.assertRaises(P.PublishError):
            P.PublishSpec.from_payload({
                "accountRef": "a", "content": {"type": "story", "title": "t"},
            })


class PublishArgvTest(unittest.TestCase):
    def test_video_argv_carries_file_title_desc_tags_schedule_thumbnail(self):
        with tempfile.TemporaryDirectory() as td:
            v = _video(Path(td))
            spec = P.PublishSpec(video=v, title="t", description="d", tags=["a"],
                                 account="acct", schedule="2026-01-01 10:00",
                                 thumbnail=v)
            argv = P.build_argv(spec)
            self.assertIn("upload-video", argv)
            self.assertIn(str(v), argv)
            self.assertEqual(argv[argv.index("--tags") + 1], "a")
            self.assertIn("--schedule", argv)
            self.assertIn("--thumbnail", argv)

    def test_note_argv_never_passes_video_flags(self):
        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "a.png"
            img.write_bytes(b"png")
            spec = P.PublishSpec(kind="note", images=[img], title="t",
                                 description="d", account="a", bgm="bgm.m4a")
            argv = P.build_argv(spec)
            self.assertIn("upload-note", argv)
            self.assertIn("--bgm", argv)
            for flag in ("--file", "--desc", "--thumbnail"):
                self.assertNotIn(flag, argv)


class PublishSubmitTest(unittest.TestCase):
    def _run(self, stdout, rc=0):
        return SimpleNamespace(returncode=rc, stdout=stdout, stderr="")

    def test_expired_cookie_fails_before_launching_the_browser(self):
        with tempfile.TemporaryDirectory() as td:
            v = _video(Path(td))
            with mock.patch.object(P, "SAU_PYTHON", v), \
                 mock.patch.object(P, "check_cookie", return_value=False), \
                 mock.patch.object(P.subprocess, "run") as run:
                with self.assertRaises(P.PublishError) as ctx:
                    P.submit("j1", {"accountRef": "a", "videoPath": str(v),
                                    "content": {"title": "t"}}, "Asia/Shanghai")
                self.assertIn("cookie", str(ctx.exception))
                run.assert_not_called()

    def test_url_in_output_is_read_back_as_remote_id(self):
        with tempfile.TemporaryDirectory() as td:
            v = _video(Path(td))
            with mock.patch.object(P, "SAU_PYTHON", v), \
                 mock.patch.object(P, "check_cookie", return_value=True), \
                 mock.patch.object(P.subprocess, "run",
                                   return_value=self._run("Submitted ok\n"
                                                          "https://www.douyin.com/video/7456000000000000000\n")):
                out = P.submit("j1", {"accountRef": "a", "videoPath": str(v),
                                      "content": {"title": "t"}}, "Asia/Shanghai")
            self.assertEqual(out["status"], "submitted_unverified")
            self.assertEqual(out["remotePostId"], "7456000000000000000")
            self.assertEqual(out["remoteUrl"], "https://www.douyin.com/video/7456000000000000000")

    def test_zero_exit_without_confirmation_is_never_success(self):
        with tempfile.TemporaryDirectory() as td:
            v = _video(Path(td))
            with mock.patch.object(P, "SAU_PYTHON", v), \
                 mock.patch.object(P, "check_cookie", return_value=True), \
                 mock.patch.object(P.subprocess, "run", return_value=self._run("nothing happened")):
                out = P.submit("j1", {"accountRef": "a", "videoPath": str(v),
                                      "content": {"title": "t"}}, "Asia/Shanghai")
            self.assertEqual(out["status"], "submission_unknown")

    def test_nonzero_exit_becomes_publish_error_with_tail(self):
        with tempfile.TemporaryDirectory() as td:
            v = _video(Path(td))
            with mock.patch.object(P, "SAU_PYTHON", v), \
                 mock.patch.object(P, "check_cookie", return_value=True), \
                 mock.patch.object(P.subprocess, "run",
                                   return_value=self._run("boom", rc=3)):
                with self.assertRaises(P.PublishError) as ctx:
                    P.submit("j1", {"accountRef": "a", "videoPath": str(v),
                                    "content": {"title": "t"}}, "Asia/Shanghai")
                self.assertIn("exited 3", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


class PublishReadbackTest(unittest.TestCase):
    """回读只读、只给三档结论：猜错会把没发出去的记成已发布。"""

    URL = "https://www.douyin.com/video/7456000000000000001"

    def _run(self, html="", status=200, error=None):
        def fetch(url, cookie, timeout):
            if error:
                raise error
            return status, html
        return P.readback(self.URL, fetch=fetch)

    def test_published_when_page_carries_the_work(self):
        result = self._run('<html><title>我的作品</title><script>{"aweme_id":"7456000000000000001"}</script></html>')
        self.assertEqual(result.state, "published")
        self.assertEqual(result.post_id, "7456000000000000001")
        self.assertEqual(result.title, "我的作品")

    def test_not_found_when_platform_says_the_work_is_gone(self):
        self.assertEqual(self._run("<html><title>抖音</title>作品不存在</html>").state, "not_found")

    def test_http_404_is_not_found_but_403_is_unknown(self):
        import urllib.error
        self.assertEqual(self._run(error=urllib.error.HTTPError(self.URL, 404, "x", {}, None)).state, "not_found")
        self.assertEqual(self._run(error=urllib.error.HTTPError(self.URL, 403, "x", {}, None)).state, "unknown")

    def test_login_wall_and_network_failure_stay_unknown(self):
        self.assertEqual(self._run("<html>登录后查看</html>").state, "unknown")
        self.assertEqual(self._run(error=TimeoutError("timed out")).state, "unknown")

    def test_page_without_marker_is_unknown_not_published(self):
        self.assertEqual(self._run("<html><title>抖音</title>随便一个页面</html>").state, "unknown")

    def test_non_work_url_is_rejected_without_fetching(self):
        called = []
        result = P.readback("https://www.douyin.com/user/abc", fetch=lambda *a: called.append(1) or (200, ""))
        self.assertEqual(result.state, "unknown")
        self.assertEqual(called, [])


class PublishWorksParseTest(unittest.TestCase):
    """作品列表只认 href 里的真 ID；抽不到就是空，不能反推「没发出去」。"""

    def test_pulls_ids_and_titles_from_the_works_page(self):
        html = (
            '<a href="/video/7456000000000000001"><span>第一条</span></a>'
            '<a href="https://www.douyin.com/video/7456000000000000001">重复</a>'
            '<a href="/note/7456000000000000002">第二条</a>'
        )
        works = P.parse_works(html)
        self.assertEqual([w["postId"] for w in works], ["7456000000000000001", "7456000000000000002"])
        self.assertEqual(works[0]["title"], "第一条")
        self.assertEqual(works[0]["url"], "https://www.douyin.com/video/7456000000000000001")

    def test_falls_back_to_bare_links_and_respects_limit(self):
        html = '<a href="/video/7456000000000000003"></a><a href="/video/7456000000000000004"></a>'
        self.assertEqual(len(P.parse_works(html, limit=1)), 1)

    def test_login_page_yields_no_works_instead_of_fake_ids(self):
        self.assertEqual(P.parse_works("<html>登录后查看</html>"), [])


class PublishReconcilePayloadTest(unittest.TestCase):
    """回读结论 → 台账回写；没有结论就不回写。"""

    def test_published_needs_a_work_id_and_carries_it(self):
        payload = P.reconcile_payload(P.Readback(state="published", post_id="7456000000000000001", title="成片"))
        self.assertEqual(payload["status"], "published")
        self.assertEqual(payload["remotePostId"], "7456000000000000001")
        self.assertEqual(payload["remoteUrl"], "https://www.douyin.com/video/7456000000000000001")
        self.assertEqual(payload["evidence"]["kind"], "readback")

    def test_published_without_id_is_refused(self):
        self.assertIsNone(P.reconcile_payload(P.Readback(state="published", post_id="")))

    def test_not_found_goes_back_to_not_submitted(self):
        payload = P.reconcile_payload(P.Readback(state="not_found", post_id="7456000000000000001"))
        self.assertEqual(payload["status"], "not_submitted")
        self.assertNotIn("remotePostId", payload)

    def test_unknown_writes_nothing(self):
        self.assertIsNone(P.reconcile_payload(P.Readback(state="unknown", post_id="7456000000000000001")))


class PublishReadbackJobTest(unittest.TestCase):
    """回读作业：明确结论才写回，其余一律只报不写。"""

    def setUp(self):
        self.sent = []

        class FakeClient:
            def report_readback(inner, attempt_id, payload):
                self.sent.append((attempt_id, payload))

        self.client = FakeClient()

    def run_job(self, state, post_id="", attempt_id="attempt-1"):
        def fake(url, account=""):
            return P.Readback(state=state, post_id=post_id, title="成片", detail="HTTP 200")
        return P.run_readback_job(
            {"url": "https://www.douyin.com/video/7456000000000000001",
             "attemptId": attempt_id}, self.client, fake)

    def test_published_is_written_back_with_the_work_id(self):
        out = self.run_job("published", "7456000000000000001")
        self.assertTrue(out["applied"])
        attempt_id, payload = self.sent[0]
        self.assertEqual(attempt_id, "attempt-1")
        self.assertEqual(payload["status"], "published")
        self.assertEqual(payload["remotePostId"], "7456000000000000001")

    def test_not_found_goes_back_to_resubmittable(self):
        out = self.run_job("not_found", "7456000000000000001")
        self.assertTrue(out["applied"])
        self.assertEqual(self.sent[0][1]["status"], "not_submitted")

    def test_unknown_is_reported_but_never_written(self):
        out = self.run_job("unknown", "7456000000000000001")
        self.assertFalse(out["applied"])
        self.assertEqual(out["reason"], "no_conclusion")
        self.assertEqual(self.sent, [])

    def test_missing_attempt_id_reports_only(self):
        out = self.run_job("published", "7456000000000000001", attempt_id="")
        self.assertFalse(out["applied"])
        self.assertEqual(out["reason"], "no_attempt")
        self.assertEqual(self.sent, [])

    def test_missing_url_is_an_error(self):
        with self.assertRaises(P.PublishError):
            P.run_readback_job({"attemptId": "a"}, self.client, lambda *a, **k: None)
