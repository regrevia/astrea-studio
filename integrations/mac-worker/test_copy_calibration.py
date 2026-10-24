#!/usr/bin/env python3
"""文案判据回归：三份基线稿必须干净，模板腔样本必须被拦住。

直接跑：python3 test_copy_calibration.py
判据一改就重跑，避免「加词表顺手把好稿也判死」。

基线稿取自 deliverables/，和线上出片用的是同一份 spec，
所以它们必须一直是 findings 全空——否则线上已经出的三条片会被体检判回。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import copy_check  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
BASELINES = (
    "deliverables/envoy-intro-script.json",
    "deliverables/script-second-topic.json",
    "deliverables/script-third-topic.json",
)


def _spoken(narration) -> list[str]:
    """口播稿可能写成一行一句的列表，也可能写成 {intro,points,outro}。"""
    if isinstance(narration, dict):
        lines: list[str] = []
        for value in narration.values():
            lines.extend(value if isinstance(value, list) else [value])
        return [str(line) for line in lines]
    return [str(line) for line in narration or []]


def _check(spec: dict) -> dict:
    return copy_check.check_copy(
        topic=spec.get("topic", ""),
        hook=spec.get("hook", ""),
        points=spec.get("points", ()),
        cta=spec.get("cta", ""),
        narration=_spoken(spec.get("narration")),
    )


def _warns(result: dict) -> list[str]:
    return [f["code"] for f in result["findings"] if f["level"] == "warn"]


def test_baselines_are_clean() -> None:
    for rel in BASELINES:
        spec = json.loads((ROOT / rel).read_text())
        spec = spec.get("spec", spec)
        result = _check(spec)
        assert result["ok"], f"{rel} 被误判：{_warns(result)}"
        assert not _warns(result), f"{rel} 出现 warn：{_warns(result)}"


def test_template_voice_is_caught() -> None:
    """套模板 + 说明书腔：三个要点全是顺序词，全文没有一个人称或反问。"""
    result = _check({
        "topic": "AI 提效的三个方法",
        "hook": "在当今这个时代，效率就是一切",
        "points": [
            "明确目标::首先，必须把目标写具体",
            "拆解任务::其次，必须把任务拆解到最小",
            "坚持复盘::最后，必须每天复盘",
        ],
        "cta": "综上所述，建议收藏",
        "narration": ["在当今这个时代效率就是一切，我们要全方位提升认知。"],
    })
    codes = {f["code"] for f in result["findings"]}
    assert "template_phrase" in _warns(result), f"漏判模板词：{sorted(codes)}"
    # 说明书腔是 info：技术稿也可能写得冷，所以只提示不拦截
    assert "flat_voice" in codes, f"漏判说明书腔：{sorted(codes)}"


def test_parallel_points_are_caught() -> None:
    """没有模板词，但三张卡是同一句骨架换名词——排比腔。"""
    result = _check({
        "topic": "选相机",
        "hook": "我劝你先看这三个数",
        "points": [
            "看底片::传感器一寸以上，底片大进光多",
            "看镜头::镜头一寸以上，进光多一圈",
            "看机身::机身一寸以上，进光多一层",
        ],
        "cta": "先看这三个数，别急着下单",
        "narration": ["传感器一寸以上，底片大进光多。"],
    })
    assert "parallel_points" in _warns(result), _warns(result)


if __name__ == "__main__":
    test_baselines_are_clean()
    test_template_voice_is_caught()
    test_parallel_points_are_caught()
    print("文案判据回归通过：3 份基线干净，模板腔与排比腔都被拦住")
