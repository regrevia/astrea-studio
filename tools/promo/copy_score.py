#!/usr/bin/env python3
"""给自动生成的稿子打分，用于「多角度 × 多 seed 里挑一版」。

不是审美模型，也不联网：只按能数出来的东西打分，同一份稿子永远同一分。

    维度（权重）：
      事实密度  dens  0.22   有事实行的镜头占比、事实行里带数字的比例
      具体性    spec  0.20   上屏数字个数、专有名词命中数（链接/公众号/PDF/浏览器…）
      推进力    adv   0.18   say 里带动作/因果标记的句子占比，减去空话词
      冗余      redun 0.15   镜头间 3-gram 重复、单句否定堆叠、同句近义重复
      节奏      pace  0.15   say 长度的离散度（太齐=念经，太乱=喘不上气）
      钩子/收尾 hook  0.10   封面有没有冲突或数字；收尾有没有给动作

    python3 tools/promo/copy_score.py /tmp/s.json
    python3 tools/promo/copy_score.py --rank --top 5            # 扫 3 角度 × 9 seed
    python3 tools/promo/copy_score.py --rank --out /tmp/best.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PY = sys.executable

DIGIT = re.compile(r"\d+(?:\.\d+)?")
# 推进论证的标记：动作、因果、转折、结果
ADV = ("打开", "读完", "读不到", "抽", "跑", "转写", "变成", "变", "给", "递",
       "装", "拿到", "因为", "所以", "于是", "最后", "然后", "而是", "不是", "只",
       "才", "按需", "续读", "录", "截图", "出处", "证据", "结论")
# 只表态、不推进：出现即扣分
FLUFF = ("赋能", "闭环", "强大", "非常好", "极致", "丝滑", "颠覆", "革命性",
         "全方位", "一站式", "极致体验", "效率提升", "降本增效", "黑科技")
# 具体名词：让话落地
CONCRETE = ("链接", "公众号", "PDF", "小红书", "B 站", "浏览器", "二维码", "关键帧",
            "转写", "出处", "页码", "标题", "时间戳", "宿主", "命令行", "分镜")
NEG = ("不", "没", "别", "无")


def _say(s: dict) -> str:
    return str(s.get("say") or "")


def _texts(s: dict) -> str:
    parts = [_say(s)]
    for k in ("head", "kicker", "pill", "sub", "note"):
        if s.get(k):
            parts.append(str(s[k]))
    for f in s.get("facts") or []:
        parts.append(str(f))
    return " ".join(parts)


def _grams(t: str, n: int = 3) -> set[str]:
    t = re.sub(r"[^\w\u4e00-\u9fff]+", "", t)
    return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}


def score(doc: dict) -> dict:
    scenes = doc.get("scenes") or []
    if not scenes:
        return {"total": 0.0}
    mids = [s for s in scenes if s.get("kind") != "cover" and s not in (scenes[-1],)]
    allsc = scenes
    says = [_say(s) for s in allsc]

    # dens：几屏有事实行、事实行里几行带数字
    with_facts = [s for s in allsc if s.get("facts")]
    fact_rows = [str(f) for s in allsc for f in (s.get("facts") or [])]
    num_rows = [f for f in fact_rows if DIGIT.search(f)]
    dens = 0.5 * (len(with_facts) / max(1, len(mids) + 1)) + \
        0.5 * (len(num_rows) / max(1, len(fact_rows)))

    # spec：上屏数字 + 具体名词
    body = " ".join(_texts(s) for s in allsc)
    n_num = len(DIGIT.findall(body))
    n_conc = sum(body.count(w) for w in CONCRETE)
    spec = 0.55 * min(1.0, n_num / 14) + 0.45 * min(1.0, n_conc / 16)

    # adv：带推进标记的句子占比，空话扣分
    hits = sum(1 for t in says if any(w in t for w in ADV))
    adv = hits / max(1, len(says))
    fluff = sum(body.count(w) for w in FLUFF)
    adv = max(0.0, adv - 0.35 * fluff)

    # redun：镜头间 3-gram 重复率 + 单句否定堆叠
    gs = [_grams(t) for t in says if t]
    dup = 0.0
    for i in range(len(gs)):
        for j in range(i + 1, len(gs)):
            if gs[i] and gs[j]:
                dup = max(dup, len(gs[i] & gs[j]) / min(len(gs[i]), len(gs[j])))
    neg_stack = sum(1 for t in says if sum(t.count(w) for w in NEG) >= 3)
    redun = max(0.0, 1.0 - 0.75 * dup - 0.25 * (neg_stack / max(1, len(says))))

    # pace：句长离散度，0.18~0.42 最好，太齐/太乱都扣
    lens = [len(t) for t in says if t]
    if len(lens) >= 2:
        mean = sum(lens) / len(lens)
        sd = (sum((x - mean) ** 2 for x in lens) / len(lens)) ** 0.5
        cv = sd / mean if mean else 0
        pace = 1.0 - min(1.0, abs(cv - 0.30) / 0.30)
    else:
        pace = 0.5
    long_pen = sum(1 for x in lens if x > 46) / max(1, len(lens))
    pace = max(0.0, pace - 0.4 * long_pen)

    # hook：封面有冲突/疑问/数字，收尾给动作
    cover, tail = scenes[0], scenes[-1]
    ch = str(cover.get("head") or "") + str(cover.get("sub") or "")
    hook_c = 1.0 if (DIGIT.search(ch) or any(w in ch for w in ("不是", "为什么", "凭什么", "假", "没"))) else 0.5
    act = ("装", "试", "用", "跑", "看", "clone", "装好")
    hook_t = 1.0 if any(w in _say(tail) for w in act) else 0.5
    hook = 0.55 * hook_c + 0.45 * hook_t

    # uniq：同一行事实不许在多个镜头重复念（重复=没推进）
    seen, dup_rows = set(), 0
    for r in fact_rows:
        if r in seen:
            dup_rows += 1
        seen.add(r)
    uniq = 1.0 - 0.6 * (dup_rows / max(1, len(fact_rows)))

    total = 100 * (0.20 * dens + 0.16 * spec + 0.16 * adv + 0.13 * redun +
                   0.12 * pace + 0.08 * hook + 0.15 * uniq)
    return {
        "total": round(total, 1),
        "dens": round(dens, 2), "spec": round(spec, 2), "adv": round(adv, 2),
        "redun": round(redun, 2), "pace": round(pace, 2), "hook": round(hook, 2),
        "uniq": round(uniq, 2),
        "digits": n_num, "say_chars": sum(len(t) for t in says if t),
    }


def _gen(angle: str, seed: int, out: Path) -> bool:
    r = subprocess.run([PY, str(HERE / "script_gen.py"), "--angle", angle,
                        "--seed", str(seed), "--out", str(out)],
                       capture_output=True, text=True)
    return r.returncode == 0


def _lint(p: Path) -> bool:
    r = subprocess.run([PY, str(HERE / "copy_lint.py"), str(p)],
                       capture_output=True, text=True)
    return r.returncode == 0


def rank(angles, seeds, workdir: Path, top: int) -> list[dict]:
    workdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for a in angles:
        for s in seeds:
            p = workdir / f"cand_{a}_{s}.json"
            if not _gen(a, s, p):
                continue
            if not _lint(p):
                continue
            d = score(json.loads(p.read_text(encoding="utf-8")))
            rows.append(dict(angle=a, seed=s, path=p, **d))
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows[:top]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("script", nargs="?", help="单份评分")
    ap.add_argument("--rank", action="store_true", help="扫角度×seed 并排序")
    ap.add_argument("--angles", default="pain,proof,candor")
    ap.add_argument("--seeds", default="0,1,2,3,4,5,6,7,8")
    ap.add_argument("--workdir", default="/tmp/promo-cand")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--out", help="把第一名写成这个文件")
    args = ap.parse_args()

    if args.rank:
        rows = rank(args.angles.split(","), [int(x) for x in args.seeds.split(",")],
                    Path(args.workdir), args.top)
        if not rows:
            print("没有候选稿通过 lint", file=sys.stderr)
            return 1
        print(f"{'angle':<7}{'seed':>5}  {'总分':>6}  密度 具体 推进 冗余 节奏 钩子 去重  数字 字数")
        for r in rows:
            print(f"{r['angle']:<7}{r['seed']:>5}  {r['total']:>6}  "
                  f"{r['dens']:.2f} {r['spec']:.2f} {r['adv']:.2f} {r['redun']:.2f} "
                  f"{r['pace']:.2f} {r['hook']:.2f} {r['uniq']:.2f}   {r['digits']:>3} {r['say_chars']:>4}")
        best = rows[0]
        if args.out:
            Path(args.out).write_text(Path(best["path"]).read_text(encoding="utf-8"), encoding="utf-8")
            print(f"\n选中 {best['angle']}/{best['seed']} → {args.out}")
        return 0

    if not args.script:
        ap.error("给一份 script.json，或加 --rank")
    d = json.loads(Path(args.script).read_text(encoding="utf-8"))
    r = score(d)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
