"""提交一份「带口播稿」的干货卡任务，验证平台→出片→草稿全程无人值守。"""
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8080/api"
TOKEN = Path("~/CreatorStudio/config/user.token").expanduser().read_text().strip()
HDR = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def call(path, data=None):
    req = urllib.request.Request(BASE + path, headers=HDR,
                                 data=json.dumps(data).encode() if data else None,
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


projects = call("/production/projects")["data"]
pid = (projects[0] if isinstance(projects, list) else projects["items"][0])["id"]

points = [
    "网页、视频、PDF 都能读::三种格式一套流程::我扔进去一个四十分钟的技术演讲，它把结论、要点、片段全给我拣出来了",
    "到底省了什么::不用下载也不用转格式::最省事的是不用先想清楚要问什么，链接丢进去它自己看",
    "记住这一条::先让它读，再让它答::重点是先把材料读完，别一上来就让模型猜",
]
spec = {
    "engine": "jianying",
    "template": "factcard",
    "topic": "Envoy 是什么",
    "hook": "你贴一个链接，它读完再告诉你结论",
    "points": points,
    "cta": "想拿它读点什么，评论区留个链接。",
    "voice": "sami:BV405_streaming",
    "kicker": "验收片",
    "narration": {"intro": "先别急着夸模型聪明，先看它到底能不能读。"},
}
run = call("/production/runs", {
    "projectId": pid,
    "title": "验收片 · Envoy 是什么（口播稿）",
    "brief": "含口播稿：开场一句、每屏一句、收尾一句",
    "engine": "jianying",
    "template": "factcard",
    "spec": spec,
})
rid = run["data"]["runId"]
print("runId", rid, flush=True)

deadline = time.time() + 600
while time.time() < deadline:
    time.sleep(6)
    got = call(f"/production/runs/{rid}")["data"]
    jobs = got.get("jobs") or []
    st = [j.get("status") for j in jobs]
    print(" ", st, flush=True)
    if st and all(s in ("succeeded", "failed", "canceled") for s in st):
        for j in jobs:
            res = j.get("result") or {}
            print(json.dumps({"status": j.get("status"), "error": j.get("lastError"),
                              "draftPath": res.get("draftPath"),
                              "stats": res.get("stats")}, ensure_ascii=False))
        sys.exit(0 if all(s == "succeeded" for s in st) else 1)
print("timeout")
sys.exit(2)
