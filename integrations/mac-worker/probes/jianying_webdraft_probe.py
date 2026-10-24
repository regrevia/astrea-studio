#!/usr/bin/env python3
"""Probe: can 剪映 11.6 load an externally generated draft that uses the
*plaintext web draft* format (root draft_info.json + plaintext draft_meta_info.json)?

Background (evidence in ~/Desktop/DSH/CreatorLab/notes/04,05):
  - drafts created by 剪映 11.6 itself / by pyJianYingDraft -> empty timeline in 11.6
  - 剪映 writes its own drafts as base64(stream-cipher) "draft_info.json"
  - a web-origin draft ("10月24日": platform.os=web, version 400000/127.0.0,
    draft_is_web_article_video=true) sits in the library as PLAINTEXT
    draft_info.json + PLAINTEXT draft_meta_info.json, and 11.6 opened it fine

So the hypothesis: 11.6 dispatches the content loader on the draft "class";
a plaintext web-class draft is readable, and we can author one.

This script never touches the source draft: it reads 10月24日/draft_info.json
as a schema donor and writes a new folder rea_probe_web01 .
"""
import json
import os
import shutil
import time
import uuid

ROOT = os.path.expanduser(
    "~/Library/Containers/com.lemon.lvpro/Data/Movies/JianyingPro/User Data/"
    "Projects/com.lveditor.draft"
)
DONOR = os.path.join(ROOT, "10月24日")
DEST = os.path.join(ROOT, "rea_probe_web01")

SCENES = [
    ("一个链接", 3.6),
    ("页面百分之九十是噪声", 4.2),
    ("切成块", 3.8),
    ("网页 PDF 视频", 4.0),
    ("envoy", 3.4),
]


def guid():
    return str(uuid.uuid4()).upper()


def hexid():
    return uuid.uuid4().hex


def main():
    base = json.load(open(os.path.join(DONOR, "draft_info.json"), encoding="utf-8"))
    donor_meta = json.load(open(os.path.join(DONOR, "draft_meta_info.json"), encoding="utf-8"))

    tpl_text = base["materials"]["texts"][0]
    tpl_seg = None
    for tr in base["tracks"]:
        if tr["type"] == "text" and tr["segments"]:
            tpl_seg = tr["segments"][0]
            tpl_track = tr
            break
    assert tpl_seg is not None, "donor has no text segment"

    texts, segs = [], []
    start = 0
    for word, dur in SCENES:
        us = int(round(dur * 1_000_000))
        t = json.loads(json.dumps(tpl_text))
        t["id"] = guid()
        content = json.loads(t["content"])
        content["text"] = word
        for st in content.get("styles", []):
            st["range"] = [0, len(word)]
        t["content"] = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
        texts.append(t)

        s = json.loads(json.dumps(tpl_seg))
        s["id"] = hexid()
        s["material_id"] = t["id"]
        s["target_timerange"] = {"start": start, "duration": us}
        s["source_timerange"] = None
        s["extra_material_refs"] = []
        segs.append(s)
        start += us

    track = json.loads(json.dumps(tpl_track))
    track["id"] = hexid()
    track["segments"] = segs
    track["name"] = "text_main"

    base["tracks"] = [track]
    base["keyframes"] = {"adjusts": [], "audios": [], "effects": [], "filters": [],
                         "handwrites": [], "stickers": [], "texts": [], "videos": []}
    base["materials"]["texts"] = texts
    for k, v in list(base["materials"].items()):
        if k != "texts" and isinstance(v, list):
            base["materials"][k] = []
    base["duration"] = start
    base["id"] = guid()
    base["name"] = "rea_probe_web01"
    now = int(time.time() * 1_000_000)
    base["create_time"] = now
    base["update_time"] = now

    if os.path.exists(DEST):
        shutil.rmtree(DEST)
    os.makedirs(DEST)

    with open(os.path.join(DEST, "draft_info.json"), "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False)
    # keep a local-schema copy so pyJianYingDraft / inspection tools can read it too
    shutil.copyfile(os.path.join(DEST, "draft_info.json"),
                    os.path.join(DEST, "draft_content.json"))

    meta = json.loads(json.dumps(donor_meta))
    meta["draft_name"] = "rea_probe_web01"
    meta["draft_fold_path"] = DEST
    meta["draft_root_path"] = ROOT
    meta["draft_cover"] = ""
    meta["draft_id"] = guid()
    meta["tm_duration"] = start
    meta["tm_draft_create"] = now
    meta["tm_draft_modified"] = now
    meta["draft_is_web_article_video"] = True
    with open(os.path.join(DEST, "draft_meta_info.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)

    # minimal companion files the app expects in a draft folder
    donor_settings = os.path.join(DONOR, "draft_settings")
    if os.path.exists(donor_settings):
        shutil.copyfile(donor_settings, os.path.join(DEST, "draft_settings"))
    with open(os.path.join(DEST, "key_value.json"), "w", encoding="utf-8") as f:
        json.dump({}, f)
    with open(os.path.join(DEST, "timeline_layout.json"), "w", encoding="utf-8") as f:
        json.dump({"dockItems": [{"dockIndex": 0, "ratio": 1,
                                 "timelineIds": [base["id"]],
                                 "timelineNames": ["时间线01"]}],
                   "layoutOrientation": 1}, f, ensure_ascii=False)

    print("wrote", DEST)
    print("tracks:", [(t["type"], len(t["segments"])) for t in base["tracks"]])
    print("duration_us:", start, "->", start / 1e6, "s")
    print("version:", base.get("version"), base.get("new_version"),
          "platform:", base.get("platform"))
    print("size draft_info.json:", os.path.getsize(os.path.join(DEST, "draft_info.json")))


if __name__ == "__main__":
    main()
