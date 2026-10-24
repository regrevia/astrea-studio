"""presenter_style 的离线单测：内容 → spec → 渲染，不联网配音。

覆盖点：
  1. 内容校验（缺 title / 缺 page 字段要报 PresenterError）
  2. spec 结构（overlay 顺序=卡片/人物/字幕；字幕条在画布内）
  3. 生成的 spec 能被 timeline_render.load_spec 接受（两个模块的契约）
  4. 有没有人物目录都能出片
  5. 端到端渲染出母版（有 ffmpeg 才跑）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import presenter_style as P
import timeline_render as T

HAS_FFMPEG = bool(shutil.which(T.FFMPEG))
HAS_MAID = Path(os.path.expanduser(str(P.DEFAULT_MAID_DIR))).is_dir()


DUR = 4.0


def fake_tts(text: str, out: Path, voice: str) -> float:
    """静音替身：不碰网络，但产物是合法 mp3，时长真实可测。

    片段别太短：timeline_render 的 loudnorm 在几秒级的极端短音上会算出 NaN，
    那是测量窗口问题，不是我们的产物问题。
    """
    subprocess.run([T.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=44100:cl=mono", "-t", str(DUR), "-q:a", "6",
                    str(out)], check=True)
    return DUR


def content(pages: int = 2, music: bool = False) -> dict:
    items = [{
        "chapter": f"第{i + 1}章",
        "title": f"这是第 {i + 1} 页的标题，稍微长一点",
        "bullets": ["要点一：可以是一句比较长的话", "要点二", "要点三"],
        "note": "一句话总结",
        "narration": "旁白文案，用来测折行是否越界，标点也不能顶格，再来一点长度凑够两行。",
    } for i in range(pages)]
    return {"title": "测试片", "series": "ASTREA TEST", "music": music,
            "pages": items}


class TestContent(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="presenter-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_title(self):
        with self.assertRaises(P.PresenterError):
            P.build_spec({"pages": content()["pages"]}, self.tmp, tts=fake_tts)

    def test_missing_pages(self):
        with self.assertRaises(P.PresenterError):
            P.build_spec({"title": "x"}, self.tmp, tts=fake_tts)

    def test_missing_narration(self):
        bad = content()
        bad["pages"][1]["narration"] = "  "
        with self.assertRaises(P.PresenterError):
            P.build_spec(bad, self.tmp, tts=fake_tts)


@unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg 生成静帧背景")
class TestSpec(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="presenter-spec-"))
        cls.spec = P.build_spec(content(pages=3), cls.tmp, tts=fake_tts,
                                maid_dir=None if HAS_MAID else cls.tmp / "nomaid")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_canvas_and_duration(self):
        self.assertEqual(self.spec["canvas"], {"width": 1920, "height": 1080, "fps": 30})
        # 3 页 × DUR + 2 个间隔，总时长要等于最后一页的 end
        total = self.spec["video"][0]["out"]
        self.assertAlmostEqual(total, 3 * DUR + 2 * P.GAP, places=2)
        self.assertAlmostEqual(self.spec["meta"]["duration"], total, places=3)

    def test_overlay_layers(self):
        ov = self.spec["overlay"]
        cards = [o for o in ov if "page" in o["src"]]
        caps = [o for o in ov if "cap" in o["src"]]
        maids = [o for o in ov if o["src"].endswith(".webm")]
        self.assertEqual(len(cards), 3)
        self.assertEqual(len(caps), 3)
        self.assertEqual(len(maids), 3 if HAS_MAID else 0)
        self.assertEqual(len(ov), len(cards) + len(caps) + len(maids))
        for i, card in enumerate(cards):
            self.assertTrue(card["src"].endswith(f"page{i:02d}.png"))
            self.assertEqual(card["enter"]["from"], "right")
            self.assertEqual(card["exit"]["to"], "left")
            cap = next(o for o in caps if o["src"].endswith(f"cap{i:02d}.png"))
            self.assertAlmostEqual(cap["start"], card["start"], places=3)
            self.assertAlmostEqual(cap["end"], card["end"], places=3)

    def test_caption_inside_canvas(self):
        from PIL import Image
        W, H = 1920, 1080
        for i in range(3):
            cap = next(o for o in self.spec["overlay"]
                       if o["src"].endswith(f"cap{i:02d}.png"))
            with Image.open(cap["src"]) as img:
                w, h = img.size
            self.assertEqual(w, P.PAGE["w"])
            self.assertLessEqual(cap["x"] + w, W)
            self.assertLessEqual(cap["y"] + h, H, "字幕条不能出画")
            self.assertGreaterEqual(cap["y"], P.PAGE["y"] + 10)

    def test_text_layer_empty(self):
        # 字幕走 PNG overlay，renderer 的文字层留空（中文折行不可控）
        self.assertEqual(self.spec["text"], [])

    def test_audio_covers_every_page(self):
        narration = [a for a in self.spec["audio"] if a.get("role") == "narration"]
        self.assertEqual(len(narration), 3)
        self.assertFalse([a for a in self.spec["audio"] if a.get("role") == "bgm"])

    def test_load_spec_accepts_it(self):
        path = self.tmp / "spec.json"
        path.write_text(json.dumps(self.spec, ensure_ascii=False), encoding="utf-8")
        spec = T.load_spec(path)
        self.assertEqual(spec["canvas"]["width"], 1920)
        self.assertEqual(spec["canvas"]["height"], 1080)
        self.assertEqual(int(spec["canvas"]["fps"]), 30)
        self.assertEqual(len(spec["overlay"]), len(self.spec["overlay"]))
        plan = T.plan(spec, self.tmp / "master.mp4", self.tmp / "work")
        self.assertTrue(any("overlay=" in a for a in plan.argv()))


@unittest.skipUnless(HAS_MAID, "本机没有 dsh-pet 人物素材目录")
class TestMaidOptional(unittest.TestCase):
    def test_runs_without_maid_dir(self):
        tmp = Path(tempfile.mkdtemp(prefix="presenter-nomaid-"))
        try:
            c = content(pages=1)
            spec = P.build_spec(c, tmp, tts=fake_tts, maid_dir=tmp / "missing")
            self.assertEqual(len(spec["overlay"]), 2)   # 卡片 + 字幕条，无人物
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_maid_clip_raises(self):
        tmp = Path(tempfile.mkdtemp(prefix="presenter-badmaid-"))
        try:
            c = content(pages=1)
            c["pages"][0]["maid"] = "不存在的动作.webm"
            with self.assertRaises(P.PresenterError):
                P.build_spec(c, tmp, tts=fake_tts)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
class TestRender(unittest.TestCase):
    def test_render_two_pages(self):
        tmp = Path(tempfile.mkdtemp(prefix="presenter-render-"))
        try:
            spec = P.build_spec(content(pages=2), tmp, tts=fake_tts,
                                maid_dir=None if HAS_MAID else tmp / "nomaid")
            path = tmp / "spec.json"
            path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            master = tmp / "master.mp4"
            ev = T.render(T.load_spec(path), master, workdir=tmp / "work")
            self.assertEqual(ev["canvas"]["width"], 1920)
            self.assertEqual(ev["canvas"]["height"], 1080)
            self.assertGreater(master.stat().st_size, 10_000)
            self.assertAlmostEqual(ev["duration"], spec["meta"]["duration"], delta=0.2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
