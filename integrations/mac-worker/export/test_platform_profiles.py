#!/usr/bin/env python3
"""platform_profiles 的单测。

跑法（仓库当前没有 pytest，两条都支持）：
    python3.11 integrations/mac-worker/export/test_platform_profiles.py
    python3 -m pytest integrations/mac-worker/export/test_platform_profiles.py -q

为什么写成「pytest 风格函数 + 自带 __main__」：
  本仓库没装 pytest（``python3 -m pytest`` 直接 ModuleNotFoundError），但
  测试是导出闭环唯一能离线验证的部分，不能因此不跑。所以每个用例都是
  ``def test_xxx():`` 且不依赖 fixture/monkeypatch：
  * 有 pytest 时它自动发现这些函数；
  * 没有 pytest 时 ``__main__`` 自己遍历执行，失败即非零退出。

为什么测试要反向加载 platform_registry：
  本模块为了零副作用复制了一份平台清单（见模块 docstring）。复制就会漂移，
  所以这里用 importlib **真读** ``../../platform_registry.py`` 的 ``LABELS``，
  断言两边 id 集合、中文名全等——上游加/删平台而本模块没跟，测试必须红。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))          # 直接 python3 跑本文件时也能 import 到同级模块

import platform_profiles as pp  # noqa: E402  (必须在 sys.path 调整之后)

REGISTRY_PATH = HERE.parent / "platform_registry.py"


def _load_registry_labels() -> dict[str, str]:
    """加载仓库真值 platform_registry.LABELS（import 期无副作用，不会跑 SAU）。"""
    assert REGISTRY_PATH.exists(), f"找不到仓库真值 {REGISTRY_PATH}"
    spec = importlib.util.spec_from_file_location("_registry_truth", REGISTRY_PATH)
    assert spec and spec.loader, "无法加载 platform_registry.py"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return dict(mod.LABELS)


# ---------------------------------------------------------------- 平台清单

def test_profiles_cover_registry_labels():
    """17 个平台一个不能少；多了也算错（多出来的 id 没有 SAU 平台对应）。"""
    truth = _load_registry_labels()
    assert len(truth) == 17, f"仓库真值应是 17 个平台，实际 {len(truth)}"
    missing = set(truth) - set(pp.PROFILES)
    extra = set(pp.PROFILES) - set(truth)
    assert not missing, f"这些平台没有出片档案: {sorted(missing)}"
    assert not extra, f"这些档案没有对应平台: {sorted(extra)}"


def test_labels_match_registry():
    """中文名必须和 registry 一致，否则面板/矩阵文档会出现两套叫法。"""
    truth = _load_registry_labels()
    for key, label in truth.items():
        assert pp.PLATFORM_LABELS[key] == label, f"{key} 中文名不一致"
        assert pp.PROFILES[key].label == label, f"{key} 档案 label 不一致"


def test_profile_fields_are_sane():
    """每个档案的字段都在合法域内，且比例与宽高真的对得上。"""
    for key, prof in pp.PROFILES.items():
        assert prof.platform == key
        assert prof.container in pp.CONTAINERS
        assert prof.bitrate_preset in pp.BITRATE_PRESETS
        assert prof.source in pp.SOURCES
        assert prof.source != pp.SOURCE_FALLBACK, f"{key} 是已知平台，不该是 fallback"
        assert pp.fps_in_panel(prof.fps), f"{key} 帧率 {prof.fps} 不在面板档位里"
        aw, ah = pp.parse_aspect(prof.aspect)
        assert abs(prof.width / prof.height - aw / ah) <= 0.01, f"{key} 比例与尺寸不符"
        assert prof.width % 2 == 0 and prof.height % 2 == 0, f"{key} 宽高应为偶数（yuv420p）"
        assert prof.notes.strip(), f"{key} 缺 notes（必须写依据/风险）"


def test_documented_requires_citation():
    """source=documented 必须给出可核对出处；当前全是 assumed，此测防以后乱标。"""
    for key, prof in pp.PROFILES.items():
        if prof.source == pp.SOURCE_DOCUMENTED:
            assert ("出处" in prof.notes or "官方文档" in prof.notes
                    or "http" in prof.notes), (
                f"{key} 标成 documented 却没写官方出处——不许把猜的写成 documented")


def test_short_video_profiles_are_portrait_or_landscape_by_design():
    """抽查几个不可能搞反的方向：抖音竖、B站横、Instagram 竖。"""
    assert pp.PROFILES["douyin"].orientation == "portrait"
    assert pp.PROFILES["bilibili"].orientation == "landscape"
    assert pp.PROFILES["instagram"].orientation == "portrait"
    assert pp.PROFILES["kuaishou"].size == (1080, 1920)


# ---------------------------------------------------------------- profile_for

def test_profile_for_unknown_returns_fallback():
    """未知平台不许抛异常：返回通用竖版并标 fallback，让流程能跑完并留痕。"""
    prof = pp.profile_for("tiktok")          # 常见但不在 SAU 17 个平台里
    assert prof.source == pp.SOURCE_FALLBACK
    assert prof.aspect == "9:16"
    assert prof.size == (1080, 1920)
    assert prof.platform == "generic"
    assert "tiktok" in prof.notes            # 请求的 id 要留痕，方便排查


def test_profile_for_unknown_empty_string():
    assert pp.profile_for("").source == pp.SOURCE_FALLBACK
    assert pp.profile_for("   ").source == pp.SOURCE_FALLBACK


def test_profile_for_is_case_insensitive():
    assert pp.profile_for("DouYin").platform == "douyin"
    assert pp.profile_for("YouTube").platform == "youtube"


def test_profile_for_keeps_declared_source():
    """已知平台原样返回，不许把 assumed 偷偷升级成别的。"""
    for key in pp.PROFILES:
        prof = pp.profile_for(key)
        assert prof == pp.PROFILES[key]


def test_profile_for_aspect_override_recomputes_size():
    """B站要竖版：保留短边 1080 的像素预算，算出 1080x1920，并把 source 降级。"""
    prof = pp.profile_for("bilibili", "9:16")
    assert prof.aspect == "9:16"
    assert prof.size == (1080, 1920)
    assert prof.source == pp.SOURCE_ASSUMED       # 覆盖是调用方意图，不是平台结论
    assert "覆盖" in prof.notes
    # 反过来：抖音要横版
    assert pp.profile_for("douyin", "16:9").size == (1920, 1080)
    # 方形
    assert pp.profile_for("douyin", "1:1").size == (1080, 1080)


def test_profile_for_same_aspect_returns_same_profile():
    base = pp.PROFILES["bilibili"]
    assert pp.profile_for("bilibili", "16:9") is base


def test_parse_aspect_accepts_common_notations():
    for text in ("9:16", "9x16", "9×16", "9/16"):
        assert pp.parse_aspect(text) == (9, 16)
    for bad in ("9-16", "", "9", "0:16"):
        try:
            pp.parse_aspect(bad)
        except ValueError:
            continue
        raise AssertionError(f"非法比例 {bad!r} 应该抛 ValueError")


# ---------------------------------------------------------------- 分辨率映射

def test_resolution_tier_boundaries():
    """面板实测档位：480P(854x480) … 8K(7680x4320)，每个档位自身都要落到自己。"""
    cases = [
        ((854, 480), "480P"),
        ((1280, 720), "720P"),
        ((1920, 1080), "1080P"),
        ((2560, 1440), "2K"),
        ((3840, 2160), "4K"),
        ((7680, 4320), "8K"),
    ]
    for (w, h), label in cases:
        got = pp.resolution_option(w, h)
        assert got == label, f"{w}x{h} 应选 {label}，实际 {got}"
        assert isinstance(got, str)
        assert got.reason.strip(), "必须附带理由"


def test_resolution_vertical_targets_use_short_side():
    """竖版目标按短边选档：1080x1920→1080P，720x1280→720P，1440x2560→2K。"""
    assert pp.resolution_option(1080, 1920) == "1080P"
    assert pp.resolution_option(720, 1280) == "720P"
    assert pp.resolution_option(1440, 2560) == "2K"
    assert pp.resolution_option(2160, 3840) == "4K"
    assert pp.resolution_option(4320, 7680) == "8K"


def test_resolution_picks_smallest_covering_tier_not_largest():
    """1600x900 用 1080P 就够，不许因为「更清晰」跳到 2K/4K（体积会爆）。"""
    assert pp.resolution_option(1600, 900) == "1080P"
    assert pp.resolution_option(1920, 1080) == "1080P"


def test_resolution_checks_both_edges_not_only_short_side():
    """1080x2400 短边是 1080，但长边 2400 > 1080P 的 1920，装不下 → 2K。"""
    got = pp.resolution_option(1080, 2400)
    assert got == "2K", f"长边超出 1080P 应升档到 2K，实际 {got}"
    assert "1080P" in got.reason or "2400" in got.reason


def test_resolution_small_target_stays_at_lowest_tier():
    """比 480P 还小的目标不降到不存在的档，最低就是 480P。"""
    assert pp.resolution_option(640, 360) == "480P"
    assert pp.resolution_option(100, 100) == "480P"


def test_resolution_original_when_canvas_matches():
    """目标与草稿画布完全一致 → 「原始」（不重采样，零画质损失）。"""
    got = pp.resolution_option(1080, 1920, canvas=(1080, 1920))
    assert got == "原始"
    assert "原始" in got.reason and "1080x1920" in got.reason


def test_resolution_not_original_when_canvas_differs():
    """画布只是同比例的不同尺寸，也不能选「原始」——原始等于画布尺寸，不是目标。"""
    got = pp.resolution_option(1080, 1920, canvas=(720, 1280))
    assert got == "1080P"
    assert "原始" not in got
    assert "720x1280" in got.reason


def test_resolution_warns_when_canvas_orientation_is_swapped():
    """竖目标 + 横画布是真实会踩的坑：导出只缩放不转比例，理由里必须报警。"""
    got = pp.resolution_option(1080, 1920, canvas=(1920, 1080))
    assert got == "1080P"
    assert "相反" in got.reason and "重建" in got.reason


def test_resolution_clamps_above_8k():
    """超出 8K 不可能「选个更大的」：clamp 到 8K 并在理由里说清会被缩小。"""
    got = pp.resolution_option(10000, 5000)
    assert got == "8K"
    assert "超出" in got.reason or "clamp" in got.reason
    assert "7680x4320" in got.reason
    assert "缩小" in got.reason or "缩到" in got.reason

    vertical = pp.resolution_option(5000, 10000)
    assert vertical == "8K"
    assert "4320" in vertical.reason


def test_resolution_rejects_invalid_dims():
    for w, h in ((0, 1080), (1080, 0), (-1, 100), (1920.0, 1080)):
        try:
            pp.resolution_option(w, h)
        except ValueError:
            continue
        raise AssertionError(f"非法尺寸 {w}x{h} 应该抛 ValueError")


def test_every_profile_maps_to_a_real_panel_label():
    """档案尺寸必须能落进真实下拉标签，且不越过 8K。"""
    labels = {label for label, _, _ in pp.RESOLUTION_TIERS} | {"原始"}
    for key, prof in pp.PROFILES.items():
        got = pp.resolution_option(prof.width, prof.height, canvas=prof.size)
        assert got in labels, f"{key} 映射出 {got!r} 不是面板标签"
        assert got == "原始", "档案尺寸与画布一致时必须是原始"
        plain = pp.resolution_option(prof.width, prof.height)
        assert plain in {label for label, _, _ in pp.RESOLUTION_TIERS}
        assert plain == "1080P", f"{key} 目前都是 1080 档，实际 {plain}"


# ---------------------------------------------------------------- 帧率映射

def test_fps_panel_labels():
    """下拉的确切标签：整数不带小数点，29.97/59.94 保留两位。"""
    assert pp.fps_option(30) == "30"
    assert pp.fps_option(24) == "24"
    assert pp.fps_option(25) == "25"
    assert pp.fps_option(50) == "50"
    assert pp.fps_option(60) == "60"
    assert pp.fps_option(120) == "120"
    assert pp.fps_option(29.97) == "29.97"
    assert pp.fps_option(59.94) == "59.94"
    assert pp.fps_option(30.0) == "30"          # float 30.0 不能变成 "30.0"


def test_fps_off_panel_snaps_to_nearest_with_reason():
    got = pp.fps_option(48)
    assert got == "50"
    assert "48" in got.reason and "最近" in got.reason


def test_fps_matches_canvas():
    got = pp.fps_option(30, canvas_fps=30)
    assert got == "30"
    assert "一致" in got.reason
    assert "重采样" not in got.reason


def test_fps_keeps_draft_rate_on_conflict():
    """平台要 30 但草稿是 25：必须返回 25 并说明面板提示，不能硬改成 30。"""
    got = pp.fps_option(30, canvas_fps=25)
    assert got == "25", f"应保留草稿帧率，实际 {got}"
    assert "草稿" in got.reason
    assert "差异" in got.reason or "重采样" in got.reason


def test_fps_distinguishes_2997_from_30():
    """29.97 与 30 在面板里是两个档位，不能当成一致。"""
    assert pp.fps_option(29.97, canvas_fps=30) == "30"
    assert pp.fps_option(30, canvas_fps=29.97) == "29.97"
    assert pp.fps_option(59.94) == "59.94"
    assert pp.fps_option(60, canvas_fps=59.94) == "59.94"


def test_fps_canvas_off_panel_is_snapped_and_logged():
    """草稿是 23.976（面板没这档）：贴到 24，但理由里必须出现原始值。"""
    got = pp.fps_option(30, canvas_fps=23.976)
    assert got == "24"
    assert "23.976" in got.reason


def test_fps_accepts_profile_values():
    for key, prof in pp.PROFILES.items():
        got = pp.fps_option(prof.fps, canvas_fps=prof.fps)
        assert got == pp._fps_label(prof.fps), f"{key} 帧率标签不对"


def test_fps_rejects_invalid_values():
    for bad in (0, -30, "30"):
        try:
            pp.fps_option(bad)  # type: ignore[arg-type]
        except ValueError:
            continue
        raise AssertionError(f"非法帧率 {bad!r} 应该抛 ValueError")


# ---------------------------------------------------------------- 返回值形态

def test_panel_option_stays_string_like():
    """PanelOption 必须能直接当字符串用（点下拉/拼日志），理由挂在 .reason。"""
    opt = pp.resolution_option(1080, 1920)
    assert isinstance(opt, str)
    assert opt == "1080P"
    assert opt.label == "1080P"
    assert str(opt) == "1080P"
    assert f"{opt}" == "1080P"
    assert "1080" in opt.reason
    assert "reason" in repr(opt)


def test_as_dict_is_json_ready_and_feeds_export_cli():
    """复现导出 CLI 的取值路径：profile_for(...).as_dict() → 分辨率/帧率标签。

    证据 JSON 要把「用的是哪份档案」落盘，所以 as_dict 必须可直接 json.dumps。
    """
    prof = pp.profile_for("douyin").as_dict()
    for key in ("platform", "label", "aspect", "width", "height", "fps",
                "container", "bitrate_preset", "notes", "source"):
        assert key in prof, f"as_dict 缺字段 {key}"
    assert json.loads(json.dumps(prof, ensure_ascii=False)) == prof
    assert pp.resolution_option(prof["width"], prof["height"]) == "1080P"
    assert pp.fps_option(prof["fps"]) == "30"
    # 未知平台与比例覆盖走同一入口，也都要出字典
    assert pp.profile_for("tiktok").as_dict()["source"] == pp.SOURCE_FALLBACK
    assert pp.profile_for("bilibili", "9:16").as_dict()["aspect"] == "9:16"


def test_generic_profile_is_not_in_profiles():
    """generic 是兜底模板，不该混进 17 个真实平台里被当成平台。"""
    assert "generic" not in pp.PROFILES
    assert pp.GENERIC_PROFILE.source == pp.SOURCE_FALLBACK


# ---------------------------------------------------------------- __main__

def main() -> int:
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    failed: list[tuple[str, BaseException]] = []
    for name, fn in tests:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001  —— 测试脚本要一次性报完所有失败
            failed.append((name, exc))
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    total = len(tests)
    print(f"\n{total - len(failed)}/{total} passed")
    if failed:
        print("失败用例:")
        for name, exc in failed:
            print(f"  - {name}: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
