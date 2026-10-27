#!/usr/bin/env python3
"""把 char_layer_lip.json 的 states（dict，键 "0".."N-1"）拍平成 lipsync_qa 要的数组。

用法：flat_lip_states.py <char_layer_lip.json> <out.json>
lipsync_qa 只认 {"fps","frames","states":[...]}；直接喂原始文件会报「口型轨缺 states 数组」。
"""
import json
import sys


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        d = json.load(fh)
    st = d.get("states")
    if st is None:
        print("错误：输入里没有 states", file=sys.stderr)
        return 1
    flat = [st[str(i)] for i in range(len(st))] if isinstance(st, dict) else list(st)
    with open(argv[2], "w", encoding="utf-8") as fh:
        json.dump({"fps": d.get("fps", 30), "frames": len(flat), "states": flat}, fh)
    print(f"{argv[2]} fps={d.get('fps', 30)} frames={len(flat)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
