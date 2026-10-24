"""主角模块（host_profile）单测：内置女仆、自定义角色目录、三种主角来源。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import host_profile as H  # noqa: E402
import presenter_style as P  # noqa: E402
import timeline_render as T  # noqa: E402

HAS_FFMPEG = bool(shutil.which(T.FFMPEG))
HAS_MAID = Path(os.path.expanduser(str(H.DEFAULT_MAID_DIR))).is_dir()


def fake_tts(text: str, out: Path, voice: str) -> float:
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_bytes(b"\x00" * 128)
    return 3.0


def make_clip(path: Path, seconds: float = 1.0, alpha: bool = True,
              size: str = "160x90") -> Path:
    """造一段测试素材：alpha 走 VP9，不透明走 H.264。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if alpha:
        cmd = [T.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
               "-i", f"testsrc2=size={size}:rate=30:duration={seconds}",
               "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
               "-b:v", "0", "-crf", "40", str(path)]
    else:
        cmd = [T.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
               "-i", f"testsrc2=size={size}:rate=30:duration={seconds}",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise unittest.SkipTest(f"造素材失败: {proc.stderr[:160]}")
    return path


def content(pages: int = 2) -> dict:
    return {
        "title": "主角模块自检",
        "series": "ASTREA STUDIO",
        "music": False,
        "pages": [
            {"title": f"第 {i} 页", "narration": f"这是第 {i} 页的解说词，用来占位。"}
            for i in range(pages)
        ],
    }


class BuiltinHostTest(unittest.TestCase):
    @unittest.skipUnless(HAS_MAID, "本机没有 dsh-pet 素材")
    def test_default_is_maid(self):
        h = H.load_host(None)
        self.assertEqual(h.id, "maid")
        self.assertEqual(h.kind, "avatar")
        self.assertTrue(h.clips)
        self.assertTrue(all(p.is_file() for p in h.clips))

    def test_unknown_host_raises(self):
        with self.assertRaises(H.HostError):
            H.load_host("根本没有这个角色")

    def test_list_hosts_returns_profiles(self):
        for h in H.list_hosts():
            self.assertIn(h.kind, H.KINDS)
            self.assertTrue(h.voice)


class ManifestHostTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="host-manifest-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, folder: str, manifest: dict) -> Path:
        d = self.tmp / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / "host.json").write_text(json.dumps(manifest, ensure_ascii=False),
                                     encoding="utf-8")
        return d

    def test_manifest_png_host(self):
        d = self._write("whale-girl", {"label": "鲸鱼娘", "kind": "avatar",
                                       "voice": "sami:BV123", "clip": "a.png"})
        (d / "a.png").write_bytes(b"not-really-a-png")
        h = H.load_host(d, hosts_dir=self.tmp)
        self.assertEqual(h.id, "whale-girl")
        self.assertEqual(h.label, "鲸鱼娘")
        self.assertEqual(h.voice, "sami:BV123")
        self.assertEqual(h.clip_for(0), d / "a.png")

    def test_manifest_missing_asset_raises(self):
        d = self._write("ghost", {"kind": "avatar", "clip": "nope.webm"})
        with self.assertRaises(H.HostError):
            H.load_host(d, hosts_dir=self.tmp)

    def test_bad_kind_raises(self):
        d = self._write("bad", {"kind": "hologram", "clip": "a.png"})
        (d / "a.png").write_bytes(b"x")
        with self.assertRaises(H.HostError):
            H.load_host(d, hosts_dir=self.tmp)

    def test_dict_override_voice_and_placement(self):
        h = H.load_host({"id": "maid", "voice": "edge:zh-CN-XiaoxiaoNeural",
                         "placement": {"width": 640}})
        self.assertEqual(h.voice, "edge:zh-CN-XiaoxiaoNeural")
        self.assertEqual(h.placement["width"], 640)
        self.assertEqual(h.placement["x"], H.DEFAULT_PLACEMENT["x"])

    def test_list_hosts_includes_manifest_dir(self):
        d = self._write("whale-girl", {"label": "鲸鱼娘", "kind": "person",
                                       "clip": "a.mp4", "alpha": False})
        (d / "a.mp4").write_bytes(b"x")
        ids = [h.id for h in H.list_hosts(hosts_dir=self.tmp)]
        self.assertIn("whale-girl", ids)


@unittest.skipUnless(HAS_FFMPEG, "本机没有 ffmpeg")
class ClipPipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="host-clip-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_still_clip_keeps_alpha(self):
        from PIL import Image
        png = self.tmp / "a.png"
        Image.new("RGBA", (64, 64), (255, 0, 0, 128)).save(png)
        out = H.still_clip(png, self.tmp / "a.webm", 1.0, 30)
        self.assertTrue(out.is_file())
        # VP9 的 alpha 记在 alpha_mode 标签里，像素格式仍显示 yuv420p
        proc = subprocess.run(
            [T.FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream_tags=alpha_mode", "-of", "csv=p=0", str(out)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.stdout.strip(), "1")

    def test_person_host_gets_frame(self):
        clip = make_clip(self.tmp / "person.mp4", alpha=False, size="320x180")
        host = H.HostProfile(id="person", label="真人", kind="person",
                             clip=clip, alpha=False)
        spec = P.build_spec(content(1), self.tmp / "out", host=host,
                            tts=fake_tts, work=self.tmp / "w")
        frames = [o for o in spec["overlay"] if "hostframe" in o["src"]]
        self.assertEqual(len(frames), 1)
        self.assertAlmostEqual(frames[0]["width"], host.placement["width"] + H.FRAME_PAD * 2)
        self.assertEqual(spec["meta"]["host"]["kind"], "person")

    def test_alpha_avatar_has_no_frame(self):
        clip = make_clip(self.tmp / "avatar.webm", alpha=True)
        host = H.HostProfile(id="avatar", label="虚拟形象", kind="avatar",
                             clips=[clip], alpha=True)
        spec = P.build_spec(content(1), self.tmp / "out2", host=host,
                            tts=fake_tts, work=self.tmp / "w2")
        self.assertFalse([o for o in spec["overlay"] if "hostframe" in o["src"]])
        self.assertTrue([o for o in spec["overlay"] if o["src"].endswith(".webm")])

    def test_host_voice_used_when_content_silent(self):
        clip = make_clip(self.tmp / "a.webm", alpha=True)
        host = H.HostProfile(id="v", label="v", kind="avatar", clips=[clip],
                             voice="edge:zh-CN-YunxiNeural")
        spec = P.build_spec(content(1), self.tmp / "out3", host=host,
                            tts=fake_tts, work=self.tmp / "w3")
        self.assertEqual(spec["meta"]["voice"], "edge:zh-CN-YunxiNeural")

    def test_poster_only_avatar_becomes_clip(self):
        from PIL import Image
        png = self.tmp / "poster.png"
        Image.new("RGBA", (96, 96), (0, 0, 255, 200)).save(png)
        host = H.HostProfile(id="poster", label="海报主角", kind="avatar",
                             poster=png, alpha=True)
        spec = P.build_spec(content(1), self.tmp / "out4", host=host,
                            tts=fake_tts, work=self.tmp / "w4")
        self.assertTrue([o for o in spec["overlay"]
                         if o["src"].endswith("host00.webm")
                         or "host00" in o["src"]])


if __name__ == "__main__":
    unittest.main()
