"""能力清单里的素材包：模型要能拿到 pack 引用，目录空/坏都不能拖垮接口。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import capabilities as cap  # noqa: E402


class MaterialsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old = os.environ.get("MATERIALS_ROOT")
        os.environ["MATERIALS_ROOT"] = str(self.tmp)

    def tearDown(self):
        if self.old is None:
            os.environ.pop("MATERIALS_ROOT", None)
        else:
            os.environ["MATERIALS_ROOT"] = self.old

    def _pack(self, slug: str, shots: list[str]):
        d = self.tmp / "packs" / slug / "shots"
        d.mkdir(parents=True)
        for name in shots:
            (d / f"{name}.png").write_bytes(b"png")

    def test_materials_lists_packs_with_refs(self):
        self._pack("zf-demo", ["hero", "dash"])
        got = cap._materials()
        self.assertEqual(Path(got["root"]).resolve(), (self.tmp / "packs").resolve())
        self.assertEqual([p["slug"] for p in got["packs"]], ["zf-demo"])
        self.assertEqual(got["packs"][0]["shots"], 2)
        self.assertEqual(got["packs"][0]["refs"],
                         ["pack:zf-demo/dash", "pack:zf-demo/hero"])

    def test_materials_empty_when_no_packs(self):
        self.assertEqual(cap._materials(), {"root": str((self.tmp / "packs").resolve()), "packs": []})

    def test_capabilities_still_returns_platforms_when_materials_broken(self):
        os.environ["MATERIALS_ROOT"] = "/proc/definitely-not-here"
        payload = cap.capabilities()
        self.assertIn("platforms", payload)
        self.assertIn("materials", payload)
        self.assertEqual(payload["materials"]["packs"], [])


if __name__ == "__main__":
    unittest.main()
