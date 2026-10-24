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
    test_courtesy_still_default()
    test_baselines_have_no_clash()
    test_author_poses_are_intent()
    print("手势同步回归通过：语义优先、礼数兜底、三份基线零错配")
