#!/usr/bin/env python3
"""script_gen.py — 宣传片文案生成器：从事实库 + 叙事角度拼出 SCENES。

为什么要有这一层：卡面、配音、渲染都已经流水线化了，只有文案是手写的。
手写的问题不是慢，是**每改一版都要重核一遍数字**。这里把文案拆成两半：

  事实库（facts/*.json）  数字、说法、出处，写一次核一次，之后只读不改
  叙事层（本文件）        先说什么、后说什么、哪句当大字、用哪种手势

生成器只会把事实库里的 claim / say **原文**贴进卡面和旁白，自己不造任何数字。
`copy_lint.py` 再把这条约束独立验一遍（卡面事实必须逐字等于事实库原文、
旁白里出现的每个阿拉伯数字都必须能在事实库里找到）。

用法：
    python3 tools/promo/script_gen.py --list
    python3 tools/promo/script_gen.py --angle proof --seed 3 --out /tmp/s.json
    PROMO_ANGLE=proof PROMO_SEED=3 tools/promo/build.sh --from tts

角度（同一套事实，换讲法）：
    pain    痛点开场：AI 看起来假 → 它没打开链接 → 证据 → 怎么做 → 边界
    proof   数字开场：最能打的对比放第一屏
    candor  诚实开场：先说「不编」，再给证据
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 文案守则（写给下一个改这段的人 / 也写给任何 LLM 草稿）：
#   1 每句话都要推进论证：给信息、给因果、给结论、给动作。只为「万一被质疑」而存在
#     的句子删掉——「你自己验一下就知道」「我们没做人工判分」「仅供参考」都属此列。
#   2 不用免责声明式语气。可信度靠可核查的形态：标签（自测 / 未验证）、出处、
#     原始数字、可复现命令；读者追问时再答，不在正文里预先挡。
#   3 一个事实只标一次「自测」，不再追加一句解释它为什么可信。
#   4 数字只能来自 facts/envoy.json，词句可以换，事实不能换。
COPY_RULES = """每句话都要推进论证；只为防质疑而存在、以及免责声明式的句子一律不写。
可信度交给标签（自测/未验证）、出处、原始数字，不交给形容词或自证。"""

ROOT = Path(__file__).resolve().parents[2]
FACTS_DIR = ROOT / "tools/promo/facts"


# ---------------------------------------------------------------- 通用
def pick(items, seed: int, salt: str = ""):
    """确定性取样：同一个 seed 永远同一套文案，方便 A/B 与复现。
    用乘法散列而不是线性取模，seed 只差 1 也能落到不同选项上（原来只在奇偶间跳）。"""
    h = 2166136261
    for i, c in enumerate(salt):
        h = ((h ^ (ord(c) + i)) * 16777619) & 0xFFFFFFFF
    h = ((h ^ (seed * 2654435761)) * 16777619) & 0xFFFFFFFF
    return items[h % len(items)]


def _load(topic: str) -> dict:
    p = FACTS_DIR / f"{topic}.json"
    if not p.exists():
        sys.exit(f"没有这个事实库: {p}\n先建一份 {FACTS_DIR}/<topic>.json")
    return json.loads(p.read_text(encoding="utf-8"))


def _claim(f: dict, fid: str) -> str:
    return f["facts"][fid]["claim"]


def _say_of(f: dict, fid: str) -> str:
    return f["facts"][fid]["say"]


def _source(f: dict, fid: str) -> str:
    return f["facts"][fid]["source"]


# ---------------------------------------------------------------- 各屏
# 每屏只依赖「角度」，不关心自己被排在第几屏；拼装时再编号。

COVERS = {
    "pain": [
        dict(head="AI 写的，你不信", sub="因为它没打开过那条链接。",
             say="AI 写的东西，你总觉得不对。 说白了，它没打开过那条链接，全靠脑补。",
             poses=["think", "point"]),
        dict(head="AI 不是不会写", sub="是那条链接，它没打开。",
             say="AI 写的报告，为什么一看就假？ 因为它压根没打开那条链接。",
             poses=["wave", "point"]),
        dict(head="AI 写的，一看就假", sub="因为它没看过原文。",
             say="AI 写的东西，为什么一眼就假？ 它没读过原文，全靠脑补。",
             poses=["think", "point"]),
    ],
    "proof": [
        dict(head="88 秒 → 48 秒", sub="同一模型，只多一层。",
             say="同一模型、同一个问题：不加这层要 88 秒才拿到证据，加上之后 48 秒。",
             poses=["point", "approve"]),
        dict(head="快了一半", sub="拿到答案：88 秒 → 48 秒。",
             say="让 AI 找证据，第一次拿到答案要 88 秒；加一层之后 48 秒。",
             poses=["point", "approve"]),
        dict(head="68% → 0%", sub="一次 A/B 自测，两条曲线。",
             say="同一个模型，做同一件事，跑出两种结果。 差别只在一层。",
             poses=["think", "point"]),
    ],
    "candor": [
        dict(head="它敢说没读到", sub="别的工具会先编一段。",
             say="大多数工具读不到就编一段给你。 它读不到就告诉你读不到，然后停在那儿。",
             poses=["shrug", "point"]),
        dict(head="读不到\n就说读不到", sub="不猜，也不编。",
             say="大多数工具读不到也会编一段给你。 它不编：读不到，就说读不到。",
             poses=["shrug", "point"]),
        dict(head="不猜", sub="读不到就直说。",
             say="它不猜。读不到、要登录、被拦，都会直说，不拿话术填空。",
             poses=["think", "point"]),
    ],
}


def s_what(f: dict, seed: int) -> dict:
    pill = " · ".join(f["capabilities"]["formats"][:4])
    return dict(
        kind="point",
        head=pick(["给 AI 一双眼睛", "它替你打开每个链接", "把链接丢给它"], seed, "what"),
        kicker="能读什么", pill=pill,
        facts=[_claim(f, "reads_coverage"), _claim(f, "reads_body")],
        note=_source(f, "reads_coverage"),
        head_size=88, head_y=470, pill_size=36,
        say=pick([
            "网页、公众号、PDF、小红书、B 站，它自己打开、自己读完，最后把出处一并给你。",
            "网页、公众号、PDF、小红书、B 站，一个个真打开、真读完，原文和出处一起留给你。",
            "给它一条链接，它自己打开、读完正文，把关键段落连出处一起带回来。",
        ], seed, "whatsay"),
        poses=["point", "approve"])


def s_proof(f: dict, seed: int) -> dict:
    heads = ["跑到超时也没结论", "A/B 差了多远"]
    extra = pick(["time_ab", "keyword_ab"], seed, "proofpair")
    return dict(
        kind="point", head=pick(heads, seed, "proofhead"),
        kicker="评测", pill="同一模型，装不装它",
        facts=[_claim(f, "timeout_ab"), _claim(f, "time_ab"), _claim(f, "keyword_ab")],
        note=_source(f, "timeout_ab"),
        head_size=92, head_y=470,
        say=pick([
            "同一个模型：" + _say_of(f, "timeout_ab") + " " + _say_of(f, extra),
            "同一个模型、同一批题，装不装它差这么多：" + _say_of(f, extra) + " " + _say_of(f, "timeout_ab"),
        ], seed, "proofsay"),
        poses=["think", "point"], exprs={1: "sparkle"})


def s_how(f: dict, seed: int) -> dict:
    mech = f["capabilities"]["mechanics"]
    return dict(
        kind="point", head=pick(["浏览器在后台自己开", "它自己会开浏览器", "后台就有浏览器"], seed, "how"),
        kicker="怎么做到", pill=f["capabilities"]["login"],
        facts=[mech[1], mech[2]],
        note="浏览器在后台跑，遇到登录才请你接管",
        head_size=88, head_y=470,
        say=pick([
            "它在后台开浏览器、抽关键帧、跑本地转写；读不完就按需续读，不重拉整页；卡在登录就把二维码递给你。",
            "后台开浏览器、抽关键帧、本地转写；没读完接着读下一页，不整页重拉；要登录就把二维码递给你。",
        ], seed, "howsay"),
        poses=["point", "approve"])


def s_candor(f: dict, seed: int) -> dict:
    return dict(
        kind="point", head=pick(["读不到就说读不到", "不猜，也不编", "它敢说没读到"], seed, "candor"),
        kicker="边界", pill="读不到就直说",
        facts=[_claim(f, "empty_ab")],
        note=_source(f, "empty_ab"),
        head_size=88, head_y=470,
        say=pick([
            "读不到就直说，不拿猜测填空。 " + _say_of(f, "empty_ab"),
            "读不到、要登录、被拦，它都直说，不用话术填空。 " + _say_of(f, "empty_ab"),
        ], seed, "candorsay"),
        poses=["shrug", "point"])


def s_cta(f: dict, seed: int) -> dict:
    return dict(
        kind="cta", head=pick(["一行装好", "一行就能装"], seed, "cta"),
        pill=f["repo"], logo=True, num="",
        sub=f["closing"],
        sizes=[96, 90, 84], head_y=396, pill_size=38, sub_size=31,
        say=pick([
            "一行装好，三个宿主都能用。 " + f["closing"],
            "装一行，三个宿主都能接。 " + f["closing"],
        ], seed, "ctasay"),
        poses=["wave", "happy", "happy"], exprs={1: "sparkle"})


ANGLES = {
    "pain": dict(order=["what", "proof", "how", "candor"],
                 lead="痛点开场：先承认「AI 写的一看就假」，再翻底牌"),
    "proof": dict(order=["proof", "candor", "what", "how"],
                  lead="数字开场：最能打的对比放第一屏"),
    "candor": dict(order=["candor", "what", "proof", "how"],
                   lead="诚实开场：先把「不编」说清，再给证据"),
}
BUILDERS = dict(what=s_what, proof=s_proof, how=s_how, candor=s_candor)
ACCENTS = ["cyan", "amber", "cyan", "amber"]


def build(topic: str = "envoy", angle: str = "pain", seed: int = 0) -> dict:
    if angle not in ANGLES:
        sys.exit(f"未知角度: {angle}（可选 {', '.join(ANGLES)}）")
    f = _load(topic)
    if topic != "envoy":
        sys.exit("目前只有 envoy 的事实库；新增主题就加 tools/promo/facts/<topic>.json")

    cov = dict(COVERS[angle][seed % len(COVERS[angle])])
    used = {cov["head"]}
    mid = []
    for role in ANGLES[angle]["order"]:
        sc = BUILDERS[role](f, seed)
        for bump in range(1, 5):          # 标题撞车就换一版说法，不硬塞
            if sc["head"] not in used:
                break
            sc = BUILDERS[role](f, seed + bump)
        used.add(sc["head"])
        mid.append(sc)

    for _ in range(6):                    # 中段标题别三屏同字开头
        first = {}
        for sc in mid:
            first[sc["head"][0]] = first.get(sc["head"][0], 0) + 1
        worst = max(first, key=first.get) if first else None
        if not worst or first[worst] < 3:
            break
        for i, role in enumerate(ANGLES[angle]["order"]):
            if mid[i]["head"][0] != worst:
                continue
            for bump in range(1, 7):
                alt = BUILDERS[role](f, seed + bump)
                if alt["head"][0] != worst and alt["head"] not in used:
                    used.add(alt["head"])
                    mid[i] = alt
                    break
            break

    for i, sc in enumerate(mid):
        sc["num"] = f"{i + 1:02d} / 04"
        sc["accent"] = ACCENTS[i % len(ACCENTS)]

    scenes = [dict(kind="cover", logo=True, num="", accent="cyan",
                   sizes=[92, 86, 80], head_y=396, sub_size=40, sub_w=820, **cov)]
    scenes += mid
    scenes.append(s_cta(f, seed))

    for i, sc in enumerate(scenes):
        sc["audio"] = f"a{i:02d}.mp3"

    beats = sum(len([x for x in sc["say"].split("。") if x.strip()]) for sc in scenes)
    return dict(version=1, topic=topic, product=f["product"], angle=angle, seed=seed,
                lead=ANGLES[angle]["lead"], scenes=scenes,
                stats=dict(scenes=len(scenes), say_chars=sum(len(sc["say"]) for sc in scenes),
                           beats=beats))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="envoy")
    ap.add_argument("--angle", default="pain")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list:
        for k, v in ANGLES.items():
            print(f"{k:8s} {v['lead']}")
        return 0

    doc = build(a.topic, a.angle, a.seed)
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        s = doc["stats"]
        print(f"{out}  angle={a.angle} seed={a.seed}  {s['scenes']} 屏 / "
              f"{s['say_chars']} 字 / {s['beats']} 拍")
    else:
        for sc in doc["scenes"]:
            print(f"[{sc['num'] or '--':7s}] {sc['head']}  | {sc.get('pill') or ''}")
            print(f"          {sc['say']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
