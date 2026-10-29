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
