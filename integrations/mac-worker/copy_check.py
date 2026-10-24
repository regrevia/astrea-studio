"""文案体检：把「像人写的」拆成能自动判的几条——套模板词、节奏、断句、空词。

不是评分器，是提醒器：出片前跑一遍，命中的地方自己看要不要改。
用法：
  python3 copy_check.py --hook "…" --point "标题::正文" --cta "…" [--narration 一行一句的稿子]
  python3 -c "import copy_check; print(copy_check.check_copy(...))"
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from typing import Any, Iterable

# 一耳朵能听出是模板的词。命中就提示，不禁止——真需要时作者自己决定。
TEMPLATE_PHRASES = (
    "首先", "其次", "再次", "最后一点", "综上所述", "总而言之", "总之",
    "众所周知", "不言而喻", "值得一提的是", "不得不说", "据悉", "据了解",
    "在当今", "随着", "的发展", "让我们一起", "接下来我们", "下面我们",
    "干货", "赋能", "抓手", "闭环", "生态位", "壁垒", "护城河", "玩法",
    "全方位", "多维度", "深度解析", "一文读懂", "建议收藏",
)

# 说了等于没说的名词，通常可以换成具体动作。
EMPTY_NOUNS = ("价值", "能力", "体系", "方法论", "认知", "维度", "层面", "颗粒度")

# 句子结束符。中文文案的呼吸主要靠标点。
SENT_END = "。！？…；"


def _sentences(texts: Iterable[str]) -> list[str]:
    out: list[str] = []
    for t in texts:
        if not t:
            continue
        for part in re.split(rf"[{SENT_END}]", str(t)):
            part = part.strip()
            if part:
                out.append(part)
    return out


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def check_copy(*, hook: str = "", points: Iterable[str] = (), cta: str = "",
               narration: Iterable[str] = (), topic: str = "") -> dict[str, Any]:
    """返回 {findings:[{code,level,where,detail}], metrics:{...}}。
    points 每项允许 "标题::正文" 形式。"""
    points = list(points)
    bodies = [p.split("::", 1)[1] if "::" in p else p for p in points]
    spoken = [s for s in narration if s]
    surfaces = {
        "开场": _squash(hook),
        "标题": _squash(topic),
        "收尾": _squash(cta),
        "要点": " ".join(_squash(b) for b in bodies),
        "口播": " ".join(_squash(s) for s in spoken),
    }

    findings: list[dict[str, Any]] = []

    def add(code: str, level: str, where: str, detail: str) -> None:
        findings.append({"code": code, "level": level, "where": where, "detail": detail})

    for where, text in surfaces.items():
        if not text:
            continue
        for phrase in TEMPLATE_PHRASES:
            if phrase in text:
                add("template_phrase", "warn", where, f"出现「{phrase}」，读起来像模板")

    full = " ".join(v for v in surfaces.values() if v)
    for noun in EMPTY_NOUNS:
        if noun in full:
            add("empty_noun", "info", "全文", f"「{noun}」可以换成具体动作")

    sents = _sentences([hook, topic, cta, *bodies, *spoken])
    lengths = [len(s) for s in sents]
    if lengths:
        metrics = {
            "sentences": len(sents),
            "avg_len": round(statistics.mean(lengths), 1),
            "min_len": min(lengths),
            "max_len": max(lengths),
            "spread": max(lengths) - min(lengths),
        }
    else:
        metrics = {"sentences": 0, "avg_len": 0, "min_len": 0, "max_len": 0, "spread": 0}

    if lengths and metrics["spread"] < 5:
        add("flat_rhythm", "warn", "全文",
            f"句子长度都在 {metrics['min_len']}-{metrics['max_len']} 字，没有长短交替，听着平")
    if lengths and metrics["min_len"] > 14:
        add("no_short_line", "warn", "全文", "没有一句短句，缺呼吸口")
    longs = [s for s in sents if len(s) > 32]
    if longs:
        add("long_line", "warn", "全文", f"{len(longs)} 句超过 32 字，一口气念不完：「{longs[0][:18]}…」")

    for i, s in enumerate(spoken, start=1):
        if len(s) > 34:
            add("long_spoken", "warn", f"口播第{i}句", f"{len(s)} 字，字幕会被切成三段以上")
        if re.search(r"[，；]", s) is None and len(s) > 20:
            add("no_comma", "info", f"口播第{i}句", "长句里没有逗号，TTS 只能硬断")

    for i, (title, body) in enumerate(
            (p.split("::", 1) if "::" in p else (p, "") for p in points), start=1):
        if body and len(_squash(body)) > 26:
            add("point_body_long", "info", f"要点{i}", f"卡面正文 {len(_squash(body))} 字，会占四行以上")

    deduped, seen = [], set()
    for f in findings:
        key = (f["code"], f["where"], f["detail"])
        if key not in seen:
            seen.add(key)
            deduped.append(f)
    return {"findings": deduped, "metrics": metrics,
            "ok": not any(f["level"] == "warn" for f in deduped)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="")
    ap.add_argument("--hook", default="")
    ap.add_argument("--point", action="append", default=[])
    ap.add_argument("--cta", default="")
    ap.add_argument("--narration", action="append", default=[])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    res = check_copy(topic=args.topic, hook=args.hook, points=args.point, cta=args.cta,
                     narration=args.narration)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        m = res["metrics"]
        print(f"句子 {m['sentences']} 句，平均 {m['avg_len']} 字，最短 {m['min_len']}，最长 {m['max_len']}")
        for f in res["findings"]:
            icon = "!" if f["level"] == "warn" else "·"
            print(f"{icon} [{f['where']}] {f['detail']}")
        print("\n没有硬伤" if res["ok"] else "\n有需要看的地方")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
