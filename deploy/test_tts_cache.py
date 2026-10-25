"""配音缓存回归：同一段稿子第二次必须复用干音，不再走合成。

批量出片能不能比母版，取决于配音稳不稳：SAMI 流式音色每次采样的停顿、
尾音都有细微差别，重合成一次，口型和时长就跟着变。这里直接验缓存本身——
第二次调用必须是内存里的拷贝，不是又一次合成。

跑法：/usr/bin/python3 deploy/test_tts_cache.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "integrations" / "mac-worker"))

import slides_render as S  # noqa: E402

TEXT = "配音缓存探针，同一段稿子第二次应当复用干音。"
VOICE = "sami:BV405_streaming"
FAST = 0.5  # 秒：缓存命中是文件拷贝，超过这个数就说明又合成了一次


def main() -> int:
    cache = S._tts_cache(TEXT, VOICE)
    if cache is None:
        print("配音缓存回归失败：拿不到缓存键")
        return 1
    # 先把探针自己的缓存删掉：不删的话「冷合成」测的其实是上一次留下的干音
    cache.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as td:
        first = Path(td) / "a.mp3"
        second = Path(td) / "b.mp3"
        t0 = time.time()
        S._tts(TEXT, first, VOICE)
        cold = time.time() - t0
        if not first.exists() or first.stat().st_size < 1000:
            print("配音缓存回归失败：第一次合成没有产出音频")
            return 1
        if not cache.exists():
            print(f"配音缓存回归失败：合成后没落盘 {cache}")
            return 1
        mtime = cache.stat().st_mtime_ns
        t1 = time.time()
        S._tts(TEXT, second, VOICE)
        warm = time.time() - t1
        same = first.read_bytes() == second.read_bytes()
    if not same:
        print("配音缓存回归失败：第二次拿到的干音和第一次不是同一段")
        return 1
    if cache.stat().st_mtime_ns != mtime:
        print("配音缓存回归失败：第二次又重写了一遍缓存")
        return 1
    if warm >= FAST:
        print(f"配音缓存回归失败：第二次耗时 {warm:.2f}s，像重新合成")
        return 1
    print(f"配音缓存回归通过：冷合成 {cold:.2f}s，同稿复用 {warm * 1000:.0f}ms，字节一致")
    return 0 if _eviction_ok() else 1


def _eviction_ok() -> bool:
    """缓存要能自己收：过期删掉、超量从最久没用的删起、刚写的留下。"""
    import os
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "voice").mkdir()
        now = time.time()
        made = []
        for n, (age, size) in enumerate(((0, 400), (10 * 86400, 400), (60 * 86400, 400))):
            f = root / "voice" / f"k{n}.mp3"
            f.write_bytes(b"x" * size)
            os.utime(f, (now - age, now - age))
            made.append(f)
        old_cache, old_cap = S.TTS_CACHE, S.TTS_CACHE_MAX_BYTES
        try:
            S.TTS_CACHE, S.TTS_CACHE_MAX_BYTES = root, 300
            S.prune_tts_cache(made[0])
            left = {f.name for f in (root / "voice").iterdir()}
            if "k2.mp3" in left:
                print("配音缓存回归失败：过期干音没被清掉")
                return False
            if "k0.mp3" not in left:
                print("配音缓存回归失败：刚写入的干音被自己删了")
                return False
            if (root / "voice" / "k1.mp3").exists():
                print("配音缓存回归失败：超量后没删最久没用的")
                return False
        finally:
            S.TTS_CACHE, S.TTS_CACHE_MAX_BYTES = old_cache, old_cap
    print("配音缓存封顶通过：过期清理、超量淘汰、刚写的保留")
    return True


if __name__ == "__main__":
    sys.exit(main())
