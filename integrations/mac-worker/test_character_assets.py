"""真立绘接图回归：绿幕抠像、差分挑选、缺图降级。

    /usr/bin/python3 integrations/mac-worker/test_character_assets.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import character  # noqa: E402


def _fake(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (512, 512), (0, 255, 0))
    for y in range(96, 416):
        for x in range(96, 416):
            img.putpixel((x, y), color)
    img.save(path)


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="char-assets-"))
    _fake(root / "base/01_base_closed.png", (255, 0, 0))
    _fake(root / "base/01_base_open.png", (255, 128, 0))
    _fake(root / "poses/pose_point_closed.png", (0, 0, 255))
    _fake(root / "poses/pose_point_open.png", (0, 128, 255))
    _fake(root / "eyes/eyes_closed.png", (128, 0, 255))

    old = character.ASSET_ROOT
    character.ASSET_ROOT = root
    character._ASSET_CACHE.clear()
    try:
        fig = character.render(pose="point", mouth="closed")
        assert fig.size == (character.DES, character.DES), fig.size
        assert fig.mode == "RGBA"
        assert fig.getpixel((5, 5))[3] == 0, "绿幕没有抠干净"
        assert fig.getpixel((256, 256))[:3] == (0, 0, 255), fig.getpixel((256, 256))

        # 半开/圆唇没有专门差分时落到大张口型
        assert character.render(pose="point", mouth="half").getpixel((256, 256))[:3] == (0, 128, 255)
        assert character.render(pose="point", mouth="round").getpixel((256, 256))[:3] == (0, 128, 255)

        # idle 走 base，睁眼/闭眼各自一张
        assert character.render(mouth="closed").getpixel((256, 256))[:3] == (255, 0, 0)
        assert character.render(mouth="open").getpixel((256, 256))[:3] == (255, 128, 0)
        assert character.render(eyes="closed").getpixel((256, 256))[:3] == (128, 0, 255)

        # 没有真图的姿势退回程序化占位图，不能抛异常
        character.ASSET_ROOT = root / "nope"
        fallback = character.render(pose="shrug", mouth="round")
        assert fallback.size == (character.DES, character.DES)
        assert fallback.getpixel((5, 5))[3] == 0
    finally:
        character.ASSET_ROOT = old
        character._ASSET_CACHE.clear()

    print("真立绘接图回归通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
