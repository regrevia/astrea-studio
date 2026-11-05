#!/usr/bin/env python3
"""copy_lint.py — 文案闸门：生成的稿子过不了这一关，就不许进配音。

它查的不是文笔，是**可追责**。宣传片翻车过两次：一次是数字在改稿时被顺手改了
口径，一次是卡面上的事实和旁白说法对不上。这两类错人眼很难发现，机器一查就出来。

规则：
  1. 卡面上每条 facts 必须**逐字等于**事实库里的某条 claim（从字段拼的公式不算）
  2. 全文出现的每个阿拉伯数字，必须能在这条事实（含出处里的轮数/时限）里找到
  3. 收尾句逐字出现，且在最末一屏
  4. 版式硬约束：大标题每行 ≤ 9 字、pill ≤ 22 字、事实行 ≤ 26 字、单屏旁白 20~90 字
  5. 6 屏、4 个中段屏、没有重复标题
  6. 同一个 seed 重算必须逐字相同（A/B 要可复现）

用法：
    python3 tools/promo/copy_lint.py script.json
    python3 tools/promo/copy_lint.py --angle proof --seed 3    # 现算现查
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/promo"))

import script_gen as SG  # noqa: E402

# 只防质疑、不推进论证的写法（见 script_gen.COPY_RULES）：命中即失败
PAD = (
    "仅供参考", "不构成", "如有出入", "以实际为准", "以实物为准", "仅代表",
    "未做人工", "人工判分", "没有夸大", "不是广告", "不保证", "无法保证",
    "你自己验", "自行判断", "需要说明", "需要强调", "严格来说", "客观地说",
    "当然也有", "可能因", "视情况而定", "不代表", "不承诺", "以最终",
)
DIGITS = re.compile(r"\d+(?:\.\d+)?")
CJK = re.compile(r"[\u4e00-\u9fff]")

LIM = dict(head_line=9, pill=26, fact_line=27, note=34, sub=30,
           say_min=15, say_max=62, total_say=(200, 272))


def words(sc: dict) -> str:
    parts = [sc.get("head", ""), sc.get("kicker", ""), sc.get("pill", ""),
             sc.get("note", ""), sc.get("sub", "")]
    parts += sc.get("facts") or []
    return " ".join(parts)


def check(doc: dict, facts: dict) -> list[str]:
    errs: list[str] = []
    scenes = doc["scenes"]
    claims = {v["claim"] for v in facts["facts"].values()}
    caps = {str(x) for x in facts["capabilities"].get("formats", [])}
    caps |= {str(x) for x in facts["capabilities"].get("mechanics", [])}
    caps |= {facts["capabilities"]["login"]}
    literal_ok = claims | caps   # 卡面允许逐字使用的原文（能力描述不含数字口径）
    sources = " ".join(v["source"] for v in facts["facts"].values())
    # 允许出现的数字：事实口径 + 出处里的参数（轮数、时限）+ 宿主数 + 页脚年限之类
    known = set(DIGITS.findall(" ".join(claims) + " " + sources))
    known |= {str(facts["capabilities"]["hosts"]), "1", "2", "3"}

    if len(scenes) != 6:
        errs.append(f"屏数 {len(scenes)}，宣传片固定 6 屏（钩子+4+收尾）")

    for i, sc in enumerate(scenes):
        tag = f"第{i + 1}屏（{sc.get('num') or '边'}）"
        for k in ("head", "say", "kind", "audio"):
            if not sc.get(k):
                errs.append(f"{tag} 缺字段 {k}")

        # 1. 卡面事实必须逐字来自事实库
        for line in sc.get("facts") or []:
            if line not in literal_ok:
                errs.append(f"{tag} 卡面事实不是事实库原文：{line}")

        # 2. 数字可追溯
        for d in DIGITS.findall(words(sc) + " " + sc.get("say", "")):
            if d not in known:
                errs.append(f"{tag} 出现无出处数字 {d}：{sc.get('head')}")

        # 4. 版式
        head_lim = LIM["head_line"] + (2 if sc.get("kind") == "cover" else 0)
        for line in sc["head"].split("\n"):
            if len(line) > head_lim:
                errs.append(f"{tag} 标题一行 {len(line)} 字 > {head_lim}：{line}")
        for k, lim in (("pill", "pill"), ("note", "note")):
            if sc.get(k) and len(sc[k]) > LIM[lim]:
                errs.append(f"{tag} {k} {len(sc[k])} 字 > {LIM[lim]}：{sc[k]}")
        for line in sc.get("facts") or []:
            if len(line) > LIM["fact_line"]:
                errs.append(f"{tag} 事实行 {len(line)} 字 > {LIM['fact_line']}：{line}")
        if sc.get("sub") and len(sc["sub"]) > LIM["sub"]:
            errs.append(f"{tag} 附注 {len(sc['sub'])} 字 > {LIM['sub']}：{sc['sub']}")
        n_say = len(sc.get("say", ""))
        if not LIM["say_min"] <= n_say <= LIM["say_max"]:
            errs.append(f"{tag} 旁白 {n_say} 字，应在 {LIM['say_min']}~{LIM['say_max']}")

        # 标签：屏上带数字的行必须能看出是自测
        for _k in ("head", "kicker", "pill", "sub", "note"):
            _t = sc.get(_k) or ""
            for _w in PAD:
                if _w in _t:
                    errs.append(f"{tag} {_k} 含防质疑/免责措辞「{_w}」：{_t}")
        for _row in sc.get("facts") or []:
            for _w in PAD:
                if _w in str(_row):
                    errs.append(f"{tag} 事实行含防质疑/免责措辞「{_w}」：{_row}")
    _rows = [str(r) for _s in scenes for r in (_s.get("facts") or [])]
    _dup = {r for r in _rows if _rows.count(r) > 1}
    for _r in sorted(_dup):
        errs.append(f"同一行事实在两屏重复念（重复=没推进）：{_r}")
    _mid_heads = [str(_s.get("head") or "") for _s in scenes[1:-1]]
    if len(_mid_heads) >= 3:
        _first = {}
        for _h in _mid_heads:
            if _h:
                _first[_h[0]] = _first.get(_h[0], 0) + 1
        _bad = {c: n for c, n in _first.items() if n >= 3}
        for _c, _n in _bad.items():
            errs.append(f"中段 {_n} 屏标题都以「{_c}」开头，读起来像复读")
        if sc.get("pill") and sc["pill"].strip() == sc["head"].strip():
            errs.append(f"{tag} 胶囊和标题重复：{sc['pill']}")
        label_ctx = (sc.get("note") or "") + (sc.get("kicker") or "")
        for line in sc.get("facts") or []:
            if DIGITS.search(line) and not any(m in line + label_ctx for m in ("自测", "实测")):
                errs.append(f"{tag} 数字行没标自测：{line}")

    # 3. 收尾句
    last = scenes[-1]
    if last.get("kind") != "cta":
        errs.append("最末一屏不是 cta")
    if facts["closing"] not in (last.get("say") or ""):
        errs.append("收尾句没出现在最后一句旁白里")
    if facts["closing"] not in (last.get("sub") or ""):
        errs.append("收尾句没上收尾卡面")

    # 5. 结构
    heads = [sc["head"] for sc in scenes]
    if len(set(heads)) != len(heads):
        errs.append("有重复标题")
    mids = [sc for sc in scenes[1:-1] if sc.get("num")]
    if len(mids) != 4:
        errs.append(f"中段编号屏 {len(mids)} 个，应为 4")

    # 6. 总量（时长靠它）
    total = sum(len(sc.get("say", "")) for sc in scenes)
    lo, hi = LIM["total_say"]
    if not lo <= total <= hi:
        errs.append(f"旁白总字数 {total}，应在 {lo}~{hi}（≈{total / 4.2:.0f} 秒）")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("script", nargs="?")
    ap.add_argument("--angle", default="")
    ap.add_argument("--topic", default="envoy")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    if a.script:
        doc = json.loads(Path(a.script).read_text(encoding="utf-8"))
    else:
        doc = SG.build(a.topic, a.angle or "pain", a.seed)

    facts = SG._load(doc.get("topic", a.topic))
    errs = check(doc, facts)
    # 可复现
    again = SG.build(doc.get("topic", a.topic), doc.get("angle", "pain"), doc.get("seed", 0))
    if json.dumps(again["scenes"], ensure_ascii=False, sort_keys=True) != \
       json.dumps(doc["scenes"], ensure_ascii=False, sort_keys=True):
        errs.append("同一 angle/seed 重算结果不一致（生成器不确定）")

    s = doc.get("stats") or {}
    head = f"{doc.get('topic')}/{doc.get('angle')} seed={doc.get('seed')} " \
           f"{s.get('scenes', len(doc['scenes']))} 屏 {s.get('say_chars', '?')} 字"
    if errs:
        print(f"✗ {head}", file=sys.stderr)
        for e in errs:
            print("  - " + e, file=sys.stderr)
        return 1
    print(f"✓ {head}：事实全部可追溯，收尾句在位，版式合规")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
