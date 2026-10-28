#!/usr/bin/env python3
"""素材引用解析的自测。直接跑：python3 test_vertical_materials.py

只测「spec 里那行字符串 → 哪个文件，找不到时说什么话」：
渲染层要的是迟早真读文件，版式/时长由成片验收管，这里不重复测。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vertical_short as V  # noqa: E402


class ResolveMaterialsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        pack = self.root / "packs" / "demo"
        (pack / "shots").mkdir(parents=True)
        self.shot = pack / "shots" / "hero-1.png"
        self.shot.write_bytes(b"png-bytes")
        (pack / "index.json").write_text(json.dumps({
            "schema": "creator-studio/materials-pack/1", "slug": "demo",
            "shots": [{"name": "hero-1", "file": "shots/hero-1.png",
                       "ref": "pack:demo/hero-1"}],
        }), encoding="utf-8")
        self._old_root = V.MATERIALS_ROOT
        V.MATERIALS_ROOT = self.root

    def tearDown(self):
        V.MATERIALS_ROOT = self._old_root
        self.tmp.cleanup()

    def test_pack_ref_resolves_to_shot_file(self):
        got = V.resolve_materials(["pack:demo/hero-1"])
        self.assertEqual([(r, p) for r, p in got],
                         [("pack:demo/hero-1", self.shot.resolve())])

    def test_layout_suffix_is_stripped(self):
        # 版位后缀是版式参数，不该参与找文件
        got = V.resolve_materials(["pack:demo/hero-1#838#contain#396"])
        self.assertEqual(got[0][1], self.shot.resolve())

    def test_absolute_and_relative_paths(self):
        got = V.resolve_materials([str(self.shot), "packs/demo/shots/hero-1.png"])
        self.assertEqual([p for _, p in got], [self.shot.resolve()] * 2)

    def test_blank_lines_are_skipped(self):
        self.assertEqual(V.resolve_materials(["", "  ", None]), [])
        self.assertEqual(V.resolve_materials(None), [])

    def test_missing_file_says_which_ref_not_env_retry(self):
        with self.assertRaises(V.MaterialError) as ctx:
            V.resolve_materials(["/tmp/studio-no-such-shot.png"])
        msg = str(ctx.exception)
        self.assertIn("素材不存在", msg)
        self.assertIn("/tmp/studio-no-such-shot.png", msg)
        # FileNotFoundError 这类字样会被平台判成「本机环境问题」自动重排三次，
        # 用户等三遍还是同一句失败；缺素材是输入错，要一次说清
        for mark in ("FileNotFoundError", "No such file", "ImportError"):
            self.assertNotIn(mark, msg)

    def test_missing_pack_and_shot_list_what_exists(self):
        with self.assertRaises(V.MaterialError) as ctx:
            V.resolve_materials(["pack:demo/nope"])
        self.assertIn("hero-1", str(ctx.exception))
        with self.assertRaises(V.MaterialError) as ctx:
            V.resolve_materials(["pack:absent/x"])
        self.assertIn("没有这个素材包", str(ctx.exception))

    def test_missing_shot_name_is_a_usage_error(self):
        with self.assertRaises(V.MaterialError) as ctx:
            V.resolve_materials(["pack:demo"])
        self.assertIn("<图名>", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
