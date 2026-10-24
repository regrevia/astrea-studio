"""publish_material.py — 把渲染母版挂进平台草稿箱（S3 上传 + POST /api/material）。

渲染好的母版必须走平台自己的物料接口进草稿箱，而不是只在磁盘上留一个 mp4：
草稿箱是用户在 /studio 里唯一能直接预览、微调、发布的入口。

用法：
  python3 -m tools.publish_material --src ~/CreatorStudio/state/renders/D6.mp4 \\
      --cover ~/CreatorStudio/state/renders/H-f5.png --title "标题" --desc "描述"

鉴权用本机平台账号 token（~/CreatorStudio/config/user.token），S3 凭证复用
worker 的 load_s3_creds()，与后台 worker 上传走同一套配置。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[1]
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))
import worker  # noqa: E402  (load_s3_creds)
from s3 import upload_file  # noqa: E402

API = "http://127.0.0.1:8080/api"
TOKEN_FILE = Path.home() / "CreatorStudio/config/user.token"


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {TOKEN_FILE.read_text().strip()}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="渲染母版 mp4")
    ap.add_argument("--cover", required=True, help="封面 png")
    ap.add_argument("--title", required=True)
    ap.add_argument("--desc", default="")
    ap.add_argument("--group", default="6ad9eef7437a9be726096246")
    ap.add_argument("--key-prefix", default="studio/samples")
    ap.add_argument("--delete", nargs="*", default=[], help="上传成功后删掉的旧物料 id")
    args = ap.parse_args()

    src, cover = Path(args.src).expanduser(), Path(args.cover).expanduser()
    for path in (src, cover):
        if not path.is_file():
            print(f"!! 找不到素材 {path}", file=sys.stderr)
            return 2

    creds = worker.load_s3_creds()
    key = upload_file(creds["endpoint"], creds["access"], creds["secret"],
                      f"{args.key_prefix}/{src.name}", src)
    ckey = upload_file(creds["endpoint"], creds["access"], creds["secret"],
                       f"{args.key_prefix}/{cover.name}", cover)
    # 注意：这里必须传裸对象 key。CreateMaterial 会自己拼成 <hostUrl>/oss/<key>，
    # 再手写一层 /oss/ 会变成 /oss/oss/... 而 404（UpdateMaterial 又不接受
    # coverUrl/mediaList，写错了只能删掉重建）。
    res = call("POST", "/material", {
        "groupId": args.group,
        "title": args.title,
        "desc": args.desc,
        "type": "video",
        "coverUrl": ckey,
        "mediaList": [{"url": key, "type": "video", "thumbUrl": ckey}],
        "topics": [],
        "accountTypes": [],
    })
    data = res.get("data") or {}
    print(json.dumps({"code": res.get("code"), "id": data.get("id"),
                      "title": data.get("title"), "url": data.get("mediaList", [{}])[0].get("url")},
                     ensure_ascii=False))
    if res.get("code") != 0:
        return 1

    if args.delete:
        removed = call("DELETE", "/material/list", {"ids": args.delete})
        print(json.dumps({"deleted": args.delete, "code": removed.get("code")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
