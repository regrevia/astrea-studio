#!/usr/bin/env python3
"""手势同步回归：这句话该做什么动作，角色就该在做什么动作。

直接跑：python3 test_gesture_sync.py
改 _POSE_RULES 或 _scene_pose 的兜底顺序后必跑——否则「贴图感」是悄悄回来的。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "export"))

import jianying_engine as JE  # noqa: E402
import vertical_short as vs  # noqa: E402

ROOT = HERE.parents[1]
BASELINES = (
    "deliverables/envoy-intro-script.json",
    "deliverables/script-second-topic.json",
    "deliverables/script-third-topic.json",
)


def test_semantics_beat_courtesy() -> None:
    """开场发问、收尾说缺口，都不该还在挥手/傻笑。"""
    assert JE._scene_pose({}, 0, 5, {"say": "为什么越堆字越像念稿？"}) == "think"
    assert JE._scene_pose({}, 4, 5, {"say": "没覆盖的部分我不编"}) == "shrug"


def test_stronger_cue_wins() -> None:
    """一句话同时命中好几条线索时，按具体度打分，而不是按规则书写顺序。"""
    assert JE._keyword_pose("未覆盖的部分为什么还要硬编") == "shrug"
    assert JE._keyword_pose("这一步一定要抽帧") == "point"
    assert JE._keyword_pose("居然真的搞定了") == "happy"


def test_switch_only_between_quoted_actions() -> None:
    """换势只能换成这句话自己命中过的动作，且不能换成情绪/礼数类。

    一句话本来就带两层线索（指重点 + 说缺口）时，后半句换个手势是在演；
    只讲一件事时不换，为了动而动就是抽搐。
    """
    say = "视频里的 PPT，它看不见。Envoy 抽帧，喂给它。"
    alt = JE._second_half_pose({}, 1, 5, {"say": say})
    assert alt in JE._keyword_scores(say), alt
    assert alt in ("point", "think", "shrug"), alt
    assert JE._second_half_pose({}, 1, 5, {"say": "收藏了一千条，我一条没做。"}) is None
    assert JE._second_half_pose({}, 1, 5, {"say": "短句不换。"}) is None


def test_switch_anchor_matches_clause() -> None:
    say = "链接丢给 AI，它只读到标题。跟没读差不多。"
    assert vs._punctuation_anchor(say) == JE._clause_cut(say) / len(say)


def test_punctuation_anchor() -> None:
    """换势要落在逗号后面：锚点按字数比例给，太短或没标点就放弃。"""
    a = vs._punctuation_anchor("链接丢给 AI，它只读到标题。跟没读差不多。")
    assert a is not None and 0.25 <= a <= 0.75
    assert vs._punctuation_anchor("短") is None
    assert vs._punctuation_anchor("没有标点的一整句话就这样读完了") is None


def test_courtesy_still_default() -> None:
    assert JE._scene_pose({}, 0, 5, {"say": "这条视频只花了一分钱"}) == "wave"
    assert JE._scene_pose({}, 4, 5, {"say": "去平台上试一条"}) == "happy"


def test_baselines_have_no_clash() -> None:
    """三份基线稿的每句话都能被手势跟上，且不是全片一个姿势。"""
    for rel in BASELINES:
        script = json.loads((ROOT / rel).read_text())
        script = script.get("spec", script)
        spec = vs.build_spec(script, Path("/tmp/gesture_sync_test"), voice=script.get("voice", ""))
        gesture = spec["meta"]["gesture"]
        assert gesture["mismatch"] == 0, f"{rel} 手势错配：{gesture['clash_at']}"
        assert len(gesture["poses"]) >= 2, f"{rel} 全片一个手势：{gesture['poses']}"
        assert len(set(gesture["gains"])) >= 2, f"{rel} 全片一个动幅：{gesture['gains']}"


def test_author_poses_are_intent() -> None:
    """作者用 poses 指定手势时不算错配，不写进自检。"""
    script = {
        "hook": "为什么你越堆字越像念稿？", "topic": "念稿",
        "points": ["少写字::字少才有人听::字少一点，画面才动得起来。"],
        "cta": "去试一条", "character": {"poses": ["think"]},
    }
    spec = vs.build_spec(script, Path("/tmp/gesture_sync_test2"), voice="")
    assert spec["meta"]["gesture"]["checked"] == 0


if __name__ == "__main__":
    test_semantics_beat_courtesy()
    test_stronger_cue_wins()
    test_punctuation_anchor()
    test_switch_only_between_quoted_actions()
    test_switch_anchor_matches_clause()
    test_courtesy_still_default()
    test_baselines_have_no_clash()
    test_author_poses_are_intent()
    print("手势同步回归通过：语义优先、礼数兜底、三份基线零错配")
