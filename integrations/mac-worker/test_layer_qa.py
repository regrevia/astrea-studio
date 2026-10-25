"""成片体检里「人物发透 / 忽大忽小」这两条的自测。

判据只有两种失败方式：该报的没报（闪断又漏过去）、不该报的报了（干净片子被打回）。
三条带 alpha 的小片子分别对应：干净、中途放大、中途整段发透。
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import video_qa as QA  # noqa: E402

FFMPEG = "/opt/homebrew/bin/ffmpeg"
W, H, FPS, N = 96, 200, 15, 90


def _clip(tmp: Path, kind: str) -> Path:
    """做一条 6 秒的人物层：中线是正常站姿，40 帧起按 kind 出毛病。"""
    d = tmp / kind
    d.mkdir(parents=True, exist_ok=True)
    for i in range(N):
        tall = 150
        alpha = 255
        if kind == "resize" and 40 <= i < 50:
            tall = 190
        if kind == "thin" and 40 <= i < 88:
            alpha = 40
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        body = Image.new("RGBA", (40, tall), (200, 120, 160, alpha))
        im.alpha_composite(body, (28, H - tall))
        im.save(d / f"f{i:04d}.png")
    mov = d / "char_layer.webm"
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-framerate", str(FPS),
         "-i", str(d / "f%04d.png"), "-c:v", "libvpx-vp9",
         "-pix_fmt", "yuva420p", "-crf", "28", "-deadline", "good",
         "-cpu-used", "4", "-row-mt", "1", "-auto-alt-ref", "0", str(mov)],
        check=True, capture_output=True)
    return mov


def _codes(kind: str, tmp: Path) -> set[str]:
    mov = _clip(tmp, kind)
    findings: list[dict] = []
    QA._layer_findings({"overlay": [{"src": str(mov)}]}, findings)
    return {str(f["code"]) for f in findings}


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        clean = _codes("clean", tmp)
        big = _codes("resize", tmp)
        thin = _codes("thin", tmp)
    assert clean == set(), f"干净的片被打回：{sorted(clean)}"
    assert "character_resize" in big, f"人物放大没抓住：{sorted(big)}"
    assert "character_thin" in thin, f"人物发透没抓住：{sorted(thin)}"
    print(f'{{"ok": true, "clean": [], "resize": {sorted(big)}, "thin": {sorted(thin)}}}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
