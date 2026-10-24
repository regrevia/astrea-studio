#!/usr/bin/env python3
"""平台出片档案（platform profiles）：把「平台要求」翻译成剪映导出面板能选的档位。

为什么需要这一层：
  剪映「导出」面板**只做缩放**，画面宽高比由**草稿画布**决定——面板上那行
  「以草稿尺寸（1920x1080）导出」就是证据，草稿参数面板写的是「比例 16:9 /
  分辨率 适应」。所以「平台要求竖版 9:16」这件事在导出端根本没有开关可拨：
  必须在建草稿时就定画布。导出端能做、也必须做准的只有两件事：

    1) 把目标分辨率映射成分辨率下拉里的**确切标签**
       （480P / 720P / 1080P / 2K / 4K / 8K / 原始）；
    2) 把目标帧率映射成帧率下拉里的**确切标签**
       （24 / 25 / 29.97 / 30 / 50 / 59.94 / 60 / 120），且在与草稿帧率
       冲突时**优先保住草稿帧率**——面板自己就写着「默认使用草稿帧率，
       修改可能导致预览和导出画面有差异」，从 30 改成 25 是重采样时间轴，
       只会让画面更差。

  本模块因此是纯函数层：不 import 任何 AX/OCR/剪映相关模块，不读写文件、
  不起子进程，可以被导出流程、CLI 和单测直接调用（所以导出闭包能离线自测）。

平台清单为什么不自己发明：
  仓库真值是 ``integrations/mac-worker/platform_registry.py`` 的 ``LABELS``
  （17 个 id，与 SAU 的平台发现保持一致）。本模块复制了一份 id→中文名
  ``PLATFORM_LABELS``，理由：platform_registry 在 import 期就算绝对路径，
  而这一层要保持零副作用、可被任意宿主 import。**复制不会漂移**——测试
  ``test_profiles_cover_registry_labels`` 会反向加载真实 ``LABELS`` 并断言
  两边完全一致（多一个、少一个、中文名不一致都会红）。

source 字段的含义（不许含糊）：
  documented  平台**官方文档明载**目标尺寸/比例/帧率，且 notes 里给出可核对出处；
  assumed     按平台惯例推断，尚未核到官方原文，**下游不要当硬约束**；
  fallback    未知平台的兜底档案，只用于「别让导出流程崩」。

  现实情况：这一轮是离线做的（联网检索不可用），因此 17 个平台**全部是
  assumed**。仓库内已有旁证（web 端发布校验规则）的在 notes 里以
  ``仓库旁证: <键名>`` 标注，人工核到官方文档后把 source 改成 documented
  并把出处写进 notes 即可。**旁证不等于官方文档，不要提前改。**
"""
from __future__ import annotations

from dataclasses import dataclass, replace

SOURCE_DOCUMENTED = "documented"
SOURCE_ASSUMED = "assumed"
SOURCE_FALLBACK = "fallback"
SOURCES = (SOURCE_DOCUMENTED, SOURCE_ASSUMED, SOURCE_FALLBACK)

CONTAINERS = ("mp4", "mov")
BITRATE_PRESETS = ("推荐", "更低", "更高", "自定义")

# 平台 id → 中文名。复制自 platform_registry.LABELS，测试断言一致。
PLATFORM_LABELS: dict[str, str] = {
    "douyin": "抖音", "xiaohongshu": "小红书", "bilibili": "B站",
    "kuaishou": "快手", "tencent": "视频号", "weibo": "微博",
    "zhihu": "知乎", "baijiahao": "百家号", "hupu": "虎扑",
    "xueqiu": "雪球", "alipay": "支付宝", "dayu": "大鱼号",
    "youtube": "YouTube", "instagram": "Instagram", "facebook": "Facebook",
    "facebook-page": "Facebook 主页", "article": "通用文章",
}

# 剪映分辨率下拉的档位，按 (标签, 长边, 短边) 升序。
# 长/短边来自面板实测文案：480P(854*480)、720P(1280×720)、1080P(1920×1080)、
# 2K(2560×1440)、4K(3840×2160)、8K(7680*4320)。"原始" 不是固定档位，
# 它等于草稿尺寸，只在目标与画布完全相同时才选。
RESOLUTION_TIERS: tuple[tuple[str, int, int], ...] = (
    ("480P", 854, 480),
    ("720P", 1280, 720),
    ("1080P", 1920, 1080),
    ("2K", 2560, 1440),
    ("4K", 3840, 2160),
    ("8K", 7680, 4320),
)

# 剪映帧率下拉的固定档位（面板实测只有这几个值，不能手填）。
PANEL_FPS: tuple[float, ...] = (24.0, 25.0, 29.97, 30.0, 50.0, 59.94, 60.0, 120.0)

# 覆盖面板当前值时的提示语：导出前必须把「格式」从实测的 MOV 切到目标容器。
PANEL_FORMAT_WARNING = "面板「格式」实测当前是 MOV，导出前要切到目标容器（选项集待补）"


class PanelOption(str):
    """既是下拉标签（str），又带一句 reason（为什么选它）。

    为什么做成 str 子类而不是 dataclass / 元组：
      调用方 99% 的用法是把标签点进下拉（``click(option)`` 或 ``option == "1080P"``），
      只有 1% 要写日志/断言理由。子类化 str 让两种用法都自然：
      ``str(opt)`` / ``opt == "1080P"`` / ``f"{opt}"`` 全都对，
      理由则挂在 ``opt.reason`` 上，不会逼调用方解包元组。
    """

    __slots__ = ("_reason",)

    def __new__(cls, label: str, reason: str) -> "PanelOption":
        obj = super().__new__(cls, label)
        obj._reason = reason
        return obj

    @property
    def label(self) -> str:
        return str.__str__(self)

    @property
    def reason(self) -> str:
        return self._reason

    def __repr__(self) -> str:  # 调试时一眼看到理由
        return f"PanelOption({str(self)!r}, reason={self._reason!r})"


# ---------------------------------------------------------------- 比例/帧率工具

def parse_aspect(aspect: str) -> tuple[int, int]:
    """把 "9:16" 解析成 (9, 16)；只接受 ``a:b`` 且两边为正整数。

    为什么严格：比例最后会变成画布尺寸，写错一个字符就是整条片子白导。
    """
    text = (aspect or "").strip().lower().replace("×", ":").replace("x", ":").replace("/", ":")
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError(f"比例格式应为 a:b，得到 {aspect!r}")
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"比例两边必须是整数，得到 {aspect!r}") from None
    if a <= 0 or b <= 0:
        raise ValueError(f"比例两边必须为正，得到 {aspect!r}")
    return a, b


def _even(n: float) -> int:
    """取最近的偶数。H.264 的 yuv420p 要偶数宽高，奇数尺寸会被编码器偷偷裁 1px。"""
    v = int(round(n))
    return v if v % 2 == 0 else v + 1


def _fps_text(value: float) -> str:
    """数值 → 给人看的帧率文字：30.0→"30"，29.97→"29.97"，23.976→"23.976"。"""
    return f"{value:g}"


def _fps_label(value: float) -> str:
    """数值 → 面板下拉标签：整数不带小数点，其余保留两位（29.97/59.94）。"""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def fps_in_panel(value: float, tolerance: float = 0.005) -> bool:
    """帧率是否命中面板档位（29.97/59.94 这类非整数也算命中）。"""
    return any(abs(float(value) - f) <= tolerance for f in PANEL_FPS)


def _snap_fps(value: float) -> tuple[float, str, str]:
    """把任意帧率贴到最近的面板档位，返回 (档位数值, 标签, 附注)。

    为什么要贴：下拉是固定选项，手填不了。贴档必须留痕——附注会进 reason，
    避免「源素材 23.976 被静默改成 24」这种事后没人认账的问题。
    """
    value = float(value)
    nearest = min(PANEL_FPS, key=lambda f: abs(f - value))
    if abs(nearest - value) <= 0.005:
        return nearest, _fps_label(nearest), ""
    return nearest, _fps_label(nearest), (
        f"面板没有 {_fps_text(value)} 这个档位，取最近档位 {_fps_label(nearest)}")


@dataclass(frozen=True)
class Profile:
    """一个平台的出片目标参数。

    注意 width/height 是**建草稿时的画布尺寸**，不是导出面板的选项：
    导出端只能把它映射成最近的可覆盖档位（见 ``resolution_option``）。
    """

    platform: str
    label: str
    aspect: str          # "9:16" / "16:9" / "3:4" / "4:5" / "1:1"
    width: int
    height: int
    fps: float
    container: str       # mp4 / mov
    bitrate_preset: str  # 剪映「视频码率」下拉的值：推荐/更低/更高/自定义
    notes: str
    source: str          # documented / assumed / fallback

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"{self.platform}: 宽高必须为正，得到 {self.width}x{self.height}")
        if self.container not in CONTAINERS:
            raise ValueError(f"{self.platform}: 未知容器 {self.container!r}")
        if self.bitrate_preset not in BITRATE_PRESETS:
            raise ValueError(f"{self.platform}: 未知码率档 {self.bitrate_preset!r}")
        if self.source not in SOURCES:
            raise ValueError(f"{self.platform}: 未知 source {self.source!r}")
        if not fps_in_panel(self.fps):
            raise ValueError(f"{self.platform}: 帧率 {self.fps} 不在面板档位里 {PANEL_FPS}")
        aw, ah = parse_aspect(self.aspect)
        # 宽高必须真能对上比例，否则建出来的草稿和被点进去的档位会打架
        if abs((self.width / self.height) - (aw / ah)) > 0.01:
            raise ValueError(
                f"{self.platform}: 比例 {self.aspect} 与 {self.width}x{self.height} 不符")

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    @property
    def short_side(self) -> int:
        return min(self.width, self.height)

    @property
    def orientation(self) -> str:
        if self.height > self.width:
            return "portrait"
        if self.width > self.height:
            return "landscape"
        return "square"

    def as_dict(self) -> dict[str, object]:
        """字典视图：给导出 CLI 填面板参数、给证据 JSON 落盘用。

        为什么要它：导出流程（``jianying_export.py``）按平台的宽高/帧率/容器去点下拉，
        还要把「这次用的是哪份档案」写进证据 JSON。dataclass 不能直接 ``dict()``，
        而证据文件必须是可 JSON 序列化的纯数据，所以统一从这里出——包含 notes/source，
        事后能一眼看出这条片子是按 assumed 档案导的。
        """
        return {
            "platform": self.platform,
            "label": self.label,
            "aspect": self.aspect,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "container": self.container,
            "bitrate_preset": self.bitrate_preset,
            "notes": self.notes,
            "source": self.source,
        }


def _p(platform: str, aspect: str, width: int, height: int, fps: float,
       container: str, bitrate_preset: str, notes: str,
       source: str = SOURCE_ASSUMED) -> Profile:
    return Profile(
        platform=platform, label=PLATFORM_LABELS.get(platform, platform),
        aspect=aspect, width=width, height=height, fps=fps,
        container=container, bitrate_preset=bitrate_preset,
        notes=notes, source=source,
    )


# 17 个平台的出片档案。全部 source=assumed——见模块 docstring 里 source 的说明。
# 每条的 notes 都写清了依据/风险，人工确认时照 notes 核一遍即可。
PROFILES: dict[str, Profile] = {
    "douyin": _p(
        "douyin", "9:16", 1080, 1920, 30, "mp4", "推荐",
        "竖版短视频主档；无仓库旁证，按抖音创作规范惯例取 1080x1920/30。"
        "导出只缩放，竖版必须在建草稿时定 9:16。"),
    "xiaohongshu": _p(
        "xiaohongshu", "9:16", 1080, 1920, 30, "mp4", "推荐",
        "视频笔记用竖版 9:16；图文笔记主流是 3:4（1080x1440），本档案只覆盖视频。"
        "无仓库旁证。"),
    "bilibili": _p(
        "bilibili", "16:9", 1920, 1080, 30, "mp4", "更高",
        "主站横版投稿；竖版要走「竖屏/Story」分区且不吃主站推荐流，两条产品线不能混。"
        "码率给「更高」因为 B 站会按上传码率再转码。无仓库旁证。"),
    "kuaishou": _p(
        "kuaishou", "9:16", 1080, 1920, 30, "mp4", "推荐",
        "竖版是唯一推荐形态。仓库旁证: kwaiResolutionSuggestion（推荐 1080x1920 竖屏）。"),
    "tencent": _p(
        "tencent", "9:16", 1080, 1920, 30, "mp4", "推荐",
        "视频号以竖版为主（分享进聊天/朋友圈的形态）。仓库旁证: 仅体积/时长限制"
        "（wxSphVideoSize/…Duration），无比例规则，故比例仍是 assumed。"),
    "weibo": _p(
        "weibo", "16:9", 1920, 1080, 30, "mp4", "推荐",
        "微博视频信息流横版为主，竖版能播但会被加边/裁切，别当竖版渠道用。无仓库旁证。"),
    "zhihu": _p(
        "zhihu", "16:9", 1920, 1080, 30, "mp4", "推荐",
        "视频回答以内嵌横版为主；知乎不是短视频消费场，竖版收益不确定。无仓库旁证。"),
    "baijiahao": _p(
        "baijiahao", "16:9", 1920, 1080, 30, "mp4", "推荐",
        "百度系信息流，横版为主；无仓库旁证。"),
    "hupu": _p(
        "hupu", "16:9", 1920, 1080, 30, "mp4", "推荐",
        "社区帖内视频横版为主（球赛集锦语境）；无仓库旁证。"),
    "xueqiu": _p(
        "xueqiu", "16:9", 1920, 1080, 30, "mp4", "推荐",
        "财报/行情讲解类内容横版为主；无仓库旁证。"),
    "alipay": _p(
        "alipay", "9:16", 1080, 1920, 30, "mp4", "推荐",
        "支付宝生活号短视频竖版；无仓库旁证，风险高（发布面窄、规则不公开）。"),
    "dayu": _p(
        "dayu", "16:9", 1920, 1080, 30, "mp4", "推荐",
        "大鱼号（UC/优酷系）横版信息流；无仓库旁证。"),
    "youtube": _p(
        "youtube", "16:9", 1920, 1080, 30, "mp4", "更高",
        "主站横版 1080p SDR；Shorts 必须另建 9:16 草稿（1080x1920），"
        "导出端改不了比例。仓库旁证: youtubeResolutionSuggestion（16:9 或 9:16）。"),
    "instagram": _p(
        "instagram", "9:16", 1080, 1920, 30, "mp4", "更高",
        "Reels/Story 用 9:16；信息流视频允许 4:5~1.91:1，9:16 在信息流会被上下裁。"
        "仓库旁证: instagramReelAspectRatio（4:5~9:16）。"),
    "facebook": _p(
        "facebook", "9:16", 1080, 1920, 30, "mp4", "更高",
        "Reels/Story 用 9:16；投主站信息流要做横版，调用 profile_for("
        "\"facebook\", \"16:9\") 覆盖。仓库旁证: facebookVideoAspectRatio（9:16~4:5）。"),
    "facebook-page": _p(
        "facebook-page", "16:9", 1920, 1080, 30, "mp4", "推荐",
        "主页视频库/信息流横版；主页发 Reels 请用 facebook 档案（9:16）。"
        "无仓库旁证（仓库只区分 facebook，没区分主页）。"),
    "article": _p(
        "article", "16:9", 1920, 1080, 30, "mp4", "推荐",
        "通用文章：SAU 清单里它不是视频投稿（platform_registry 发现平台时显式排除）。"
        "此档案只给「文章里内嵌视频」兜底；纯图文不涉及导出。"),
}

GENERIC_PROFILE = Profile(
    platform="generic", label="通用竖版",
    aspect="9:16", width=1080, height=1920, fps=30.0,
    container="mp4", bitrate_preset="推荐",
    notes="未知平台的兜底：短视频生态最常见形态（竖版 1080x1920/30）。"
          "不是任何平台的官方要求，只保证流程不崩。",
    source=SOURCE_FALLBACK,
)


# ---------------------------------------------------------------- 分辨率映射

def _check_dims(width: int, height: int) -> tuple[int, int]:
    if not isinstance(width, int) or not isinstance(height, int):
        raise ValueError(f"分辨率必须是整数像素，得到 {width!r}x{height!r}")
    if width <= 0 or height <= 0:
        raise ValueError(f"分辨率必须为正，得到 {width}x{height}")
    return width, height


def resolution_option(width: int, height: int,
                      canvas: tuple[int, int] | None = None) -> PanelOption:
    """把目标分辨率映射成剪映分辨率下拉的**确切标签**，并给出理由。

    判据（按此顺序）：

      1. ``canvas`` 与目标**完全相等** → ``"原始"``：不重采样，零画质损失且最快
         （面板这时显示「以草稿尺寸（w x h）导出」）。
      2. 否则逐档升序，取**第一个「长边和短边都不小于目标」的档位**。
         只比短边会让 1080x2400（20:9）错配到 1080P（长边只有 1920，装不下）；
         只比长边会让 1080x1920 错配到 4K。两侧都覆盖才叫「装得下」。
      3. 没有任何档位装得下（超出 8K）→ **clamp 到 8K**，reason 里写明会被缩到
         7680x4320：面板没有更大的档，宁可明确告知也不要静默选个小的。
      4. 画布方向与目标相反（竖目标 + 横画布）→ 照常返回档位，但 reason 追加
         警告：导出只缩放不转比例，得重建草稿。

    为什么优先「最小的可覆盖档」而不是「最大档」：4K/8K 的码率和体积是 1080P 的
    4~16 倍，而抖音/小红书这类平台二次转码后 1080P 和 4K 肉眼无差——用最大档
    只会让导出变慢、文件变大、上传更容易超限。
    """
    w, h = _check_dims(width, height)
    t_long, t_short = max(w, h), min(w, h)

    if canvas is not None:
        if len(canvas) != 2:
            raise ValueError(f"canvas 必须是 (宽, 高)，得到 {canvas!r}")
        cw, ch = _check_dims(int(canvas[0]), int(canvas[1]))
        if (cw, ch) == (w, h):
            return PanelOption("原始", (
                f"目标 {w}x{h} 与草稿画布完全一致：选「原始」= 不重采样，"
                f"零画质损失且导出最快（面板显示「以草稿尺寸（{w}x{h}）导出」）。"))
        orient_warn = ""
        if (cw, ch) == (h, w):
            orient_warn = (
                f"⚠ 画布方向与目标相反（草稿是 {cw}x{ch} 横版，目标 {w}x{h} 竖版）："
                "导出面板只缩放、不转比例，继续导出会带黑边或被裁；"
                "正确做法是重建对应比例的草稿，而不是在导出端硬凑。")
    else:
        cw = ch = 0
        orient_warn = ""

    for label, tier_long, tier_short in RESOLUTION_TIERS:
        if tier_long >= t_long and tier_short >= t_short:
            if (tier_long, tier_short) == (t_long, t_short):
                fit = "长边短边都恰好相等"
            else:
                fit = f"长边 {tier_long}≥{t_long}、短边 {tier_short}≥{t_short}，装得下目标"
            reason = (
                f"目标 {w}x{h}（长边 {t_long}/短边 {t_short}）：按「长边与短边都要覆盖」"
                f"从 480P 往上逐档比，{label}({tier_long}x{tier_short}) 是第一个{fit}的档位；"
                "再往上（更大档）只会白白增加体积和导出耗时，平台二次转码后肉眼无差。")
            if canvas is not None:
                reason += f" 当前草稿画布 {cw}x{ch}，与目标不同，故不选「原始」。"
            return PanelOption(label, reason + orient_warn)

    label, tier_long, tier_short = RESOLUTION_TIERS[-1]
    reason = (
        f"目标 {w}x{h}（长边 {t_long}/短边 {t_short}）超出最大档 {label}"
        f"({tier_long}x{tier_short}) 的可覆盖范围，已 clamp 到 {label}：面板没有更大的档位，"
        f"实际出片会被缩到 {tier_long}x{tier_short}（严格按草稿比例，短边 {tier_short}）。"
        "若真需要更大像素，只能缩小草稿内容的放大倍数或改档位体系，导出端无解。")
    return PanelOption(label, reason + orient_warn)


# ---------------------------------------------------------------- 帧率映射

def fps_option(fps: float, canvas_fps: float | None = None) -> PanelOption:
    """把目标帧率映射成剪映帧率下拉的**确切标签**，并给出理由。

    规则：
      * 目标帧率不在面板档位（如 48）→ 贴到最近档（50），reason 留痕；
      * 给了 ``canvas_fps`` 且与目标档位**不同** → **返回草稿帧率**，reason 说明：
        面板那行写着「默认使用草稿帧率，修改可能导致预览和导出画面有差异」；
        30→25 是重采样时间轴（丢帧/重排），比「按平台建议改帧率」损失大得多。
        真需要平台帧率，应在建草稿时定（选 60fps 素材/建 60 草稿），不是改导出。
      * 两者一致（29.97 与 30 视为不同档，面板就是两行）→ 返回该档，无冲突。
    """
    if not isinstance(fps, (int, float)):
        raise ValueError(f"帧率必须是数字，得到 {fps!r}")
    if float(fps) <= 0:
        raise ValueError(f"帧率必须为正，得到 {fps}")
    target_value, target_label, target_note = _snap_fps(float(fps))
    suffix = f"（{target_note}）" if target_note else ""

    if canvas_fps is None:
        return PanelOption(target_label, (
            f"平台目标帧率 {_fps_text(float(fps))} → 面板选「{target_label}」{suffix}；"
            "未提供草稿帧率，按平台目标走。"))

    if not isinstance(canvas_fps, (int, float)):
        raise ValueError(f"草稿帧率必须是数字，得到 {canvas_fps!r}")
    if float(canvas_fps) <= 0:
        raise ValueError(f"草稿帧率必须为正，得到 {canvas_fps}")

    canvas_value, canvas_label, canvas_note = _snap_fps(float(canvas_fps))
    canvas_suffix = f"（{canvas_note}）" if canvas_note else ""

    if abs(canvas_value - target_value) < 1e-9:
        return PanelOption(target_label, (
            f"平台目标 {_fps_text(float(fps))} 与草稿帧率 {_fps_text(float(canvas_fps))} 同档"
            f"（{target_label}），一致，直接选「{target_label}」{suffix}{canvas_suffix}。"))

    return PanelOption(canvas_label, (
        f"平台建议 {target_label}，但草稿是 {_fps_text(float(canvas_fps))}：剪映帧率行默认用草稿帧率，"
        f"面板明写「修改可能导致预览和导出画面有差异」，30→25 这种改动是重采样时间轴（丢帧/重排），"
        f"损失远大于「没按平台建议」。故保留草稿帧率「{canvas_label}」{canvas_suffix}{suffix}；"
        f"确需 {target_label} 应在建草稿时定帧率（按 60 采集/建 60 草稿），不要在导出端硬改。"))


# ---------------------------------------------------------------- 平台查询/覆盖

def profile_for(platform: str, aspect: str | None = None) -> Profile:
    """查平台档案；未知平台返回 generic 竖版且 ``source="fallback"``，**不抛异常**。

    为什么未知平台不抛：导出闭环里平台 id 来自上游（SAU 发现、用户输入、缓存），
    一个新平台把整条流水线打断，代价远大于「先用通用竖版出一版、日志留痕」。

    ``aspect`` 覆盖（如给 Facebook 要横版）：保留原档案的短边像素预算，
    按新比例算宽高（取偶数），并把 source 降级成 assumed——
    覆盖是调用方意图，不是平台档案的结论，不能冒充 documented。
    """
    key = (platform or "").strip().lower()
    base = PROFILES.get(key)
    if base is None:
        return replace(
            GENERIC_PROFILE,
            source=SOURCE_FALLBACK,
            notes=GENERIC_PROFILE.notes + f"（请求的平台 {platform!r} 不在 17 个已知平台里）",
        )

    if aspect is None:
        return base
    aspect = aspect.strip()
    if aspect == base.aspect:
        return base

    aw, ah = parse_aspect(aspect)
    ratio = aw / ah
    short = base.short_side
    if ratio > 1:                      # 横版：短边给高
        width, height = _even(short * ratio), short
    elif ratio < 1:                    # 竖版：短边给宽
        width, height = short, _even(short / ratio)
    else:
        width = height = short
    return replace(
        base,
        aspect=aspect, width=width, height=height,
        source=SOURCE_ASSUMED,
        notes=(f"{base.notes}｜调用方把比例覆盖为 {aspect}（{width}x{height}），"
               "该尺寸是覆盖推导值，不是平台档案结论。"),
    )


def all_profiles() -> list[Profile]:
    """按平台 id 排序返回全部档案（不含 generic），方便生成文档/表格。"""
    return [PROFILES[k] for k in sorted(PROFILES)]


__all__ = [
    "BITRATE_PRESETS", "CONTAINERS", "GENERIC_PROFILE", "PANEL_FORMAT_WARNING",
    "PANEL_FPS", "PLATFORM_LABELS", "PROFILES", "PanelOption", "Profile",
    "RESOLUTION_TIERS", "SOURCES", "SOURCE_ASSUMED", "SOURCE_DOCUMENTED",
    "SOURCE_FALLBACK", "all_profiles", "fps_in_panel", "fps_option",
    "parse_aspect", "profile_for", "resolution_option",
]
