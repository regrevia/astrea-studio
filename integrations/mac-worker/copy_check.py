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
from difflib import SequenceMatcher
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

# 解释腔：文案里出现「也就是说」这类连接词，等于在替读者理解，PPT 文案里不该有。
EXPLAINER_PHRASES = (
    "也就是说", "换句话说", "简单来说", "简单讲", "其实", "实际上",
    "可以说", "某种程度上", "某种意义上", "换言之", "通俗地讲", "打个比方",
)

# 人写稿会露出手：称呼读者、自嘲、下判断、反问。通篇一个都没有，读起来就是说明书。
# 不收「我」：模板腔最爱用「我们要…」，把它当人味就漏判了。
VOICE_MARKS = ("你", "咱", "别", "真的", "其实", "根本", "没人", "谁",
               "说白了", "不编", "说实话", "评论区", "吧", "嘛", "？", "！", "?")

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


def _split_point(raw: str) -> tuple[str, str, str]:
    """「标题::卡面正文[::口播稿]」→ 三段；只有一段时当成正文。"""
    parts = [p.strip() for p in str(raw).split("::")]
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return "", parts[0], ""


def check_copy(*, hook: str = "", points: Iterable[str] = (), cta: str = "",
               narration: Iterable[str] = (), topic: str = "") -> dict[str, Any]:
    """返回 {findings:[{code,level,where,detail}], metrics:{...}}。
    points 每项允许 "标题::正文" 形式。"""
    points = list(points)
    # 要点是「标题::卡面正文::口播稿」三段：旧版只切第一个 ::，
    # 把口播稿也算进卡面正文，卡面明明一行却报「正文 32 字」。
    parsed = [_split_point(p) for p in points]
    bodies = [body or title for title, body, _ in parsed]
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
    for where, text in surfaces.items():
        for phrase in EXPLAINER_PHRASES:
            if phrase in text:
                add("explainer_tone", "warn", where, f"「{phrase}」是在解释，删掉更利落")
        if "（" in text or "(" in text or "——" in text:
            add("parenthetical", "info", where, "括号/破折号补充可以删或改写进主句")
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
    if lengths and metrics["min_len"] > 10:
        add("no_short_line", "warn", "全文", "没有一句 10 字以内的短句，缺呼吸口")
    longs = [s for s in sents if len(s) > 26]
    if longs:
        add("long_line", "warn", "全文", f"{len(longs)} 句超过 26 字，一口气念不完：「{longs[0][:16]}…」")

    if not any(mark in full for mark in VOICE_MARKS):
        # info 而不是 warn：技术稿可以写得很冷，这条只提醒「要不要加一句对人说的话」
        add("flat_voice", "info", "全文",
            "没有一个称呼、反问或判断，读起来像说明书")

    # 三张卡同前缀/同结尾＝排比腔，观众第三张就知道句式，后面全在等结束
    flat_bodies = [_squash(b) for b in bodies if len(_squash(b)) >= 4]
    if len(flat_bodies) >= 3:
        heads = {b[:4] for b in flat_bodies}
        tails = {b[-2:] for b in flat_bodies}
        pairs = [
            SequenceMatcher(None, a, b).ratio()
            for i, a in enumerate(flat_bodies) for b in flat_bodies[i + 1:]
        ]
        mean_ratio = sum(pairs) / len(pairs) if pairs else 0.0
        if len(heads) == 1 or len(tails) == 1 or mean_ratio >= 0.5:
            stamp = next(iter(heads if len(heads) == 1 else tails)) if (len(heads) == 1 or len(tails) == 1) else ""
            why = f"都带「{stamp}」" if stamp else f"字面相似度 {mean_ratio:.2f}"
            add("parallel_points", "warn", "要点",
                f"三张卡句式同构（{why}），换一种说法")

    for i, s in enumerate(spoken, start=1):
        if len(s) > 28:
            add("long_spoken", "warn", f"口播第{i}句", f"{len(s)} 字，字幕会被切成三段以上")
        if re.search(r"[，；]", s) is None and len(s) > 16:
            add("no_comma", "info", f"口播第{i}句", "长句里没有逗号，TTS 只能硬断")

    for i, (title, body, said) in enumerate(parsed, start=1):
        flat_body = _squash(body)
        if body and len(flat_body) > 18:
            add("point_body_long", "warn", f"要点{i}", f"卡面正文 {len(flat_body)} 字，超过一行半")
        # 卡面把口播原句贴上去，观众先读到再听到，等于剧透自己的下一句
        flat_said = _squash(said)
        if len(flat_body) >= 4 and len(flat_said) >= 4:
            if flat_body in flat_said or SequenceMatcher(
                    None, flat_body, flat_said).ratio() > 0.5:
                add("card_echoes_voice", "warn", f"要点{i}",
                    "卡面正文和口播是同一句话，读到就等于听完了")

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
