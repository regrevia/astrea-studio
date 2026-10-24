"""成片主角（主播）模块。

同一套「虚拟主播讲技术」版式，主角可以是三种来源：

* ``avatar``        虚拟形象：一串带 alpha 的 webm 动作切片（如 dsh-pet 女仆、鲸鱼娘）
* ``person``        真人：一段实拍视频或一张照片（无 alpha，自动加圆角相框）
* ``digital_human`` 数字人：TTS 合成的口型视频（有 alpha 就直通，没有就加相框）

主角决定「谁来解说」：配音音色、字幕里的自称、右下角站位都跟着 profile 走。
新增主角不需要改代码，往 ``~/CreatorStudio/state/characters/<id>/`` 放素材和
``host.json`` 即可；面板和 CLI 都通过 :func:`list_hosts` 枚举。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import timeline_render as T

HOSTS_DIR = Path(os.path.expanduser("~/CreatorStudio/state/characters"))
DEFAULT_HOST_ID = "maid"
DEFAULT_VOICE = "sami:BV411_streaming"  # 剪映「解说小帅」
DEFAULT_MAID_DIR = Path(os.path.expanduser(
    "~/.dsh/profiles/desktop/node_modules/dsh-pet/assets/webm"))

# 三种主角的默认站位（1920x1080 画布右下角），manifest 里可覆盖
DEFAULT_PLACEMENT = {"x": 1046, "y": 470, "width": 872}
DEFAULT_MOTION = {"type": "bob", "amplitude": 9, "period": 2.6}
FRAME_PAD = 26          # 无 alpha 的主角加相框时的内边距（像素）
AVATAR_SUFFIXES = (".webm", ".mov", ".mkv")
VIDEO_SUFFIXES = (".mp4", ".webm", ".mov", ".mkv")

KINDS = ("avatar", "person", "digital_human")


class HostError(RuntimeError):
    """主角配置或素材不合法。"""


@dataclass
class HostProfile:
    id: str
    label: str
    kind: str = "avatar"
    voice: str = DEFAULT_VOICE
    dir: Optional[Path] = None
    clips: list = field(default_factory=list)
    clip: Optional[Path] = None
    poster: Optional[Path] = None
    alpha: bool = True
    placement: dict = field(default_factory=lambda: dict(DEFAULT_PLACEMENT))
    motion: dict = field(default_factory=lambda: dict(DEFAULT_MOTION))
    outline: bool = True     # 无 alpha 时是否画相框
    source: str = "builtin"

    def clip_for(self, page_index: int) -> Optional[Path]:
        """第 n 页用哪个素材：虚拟形象按页轮播动作，其余主角固定一段。"""
        if self.kind == "avatar" and self.clips:
            return self.clips[page_index % len(self.clips)]
        return self.clip or (self.clips[0] if self.clips else None)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "kind": self.kind,
            "voice": self.voice, "alpha": self.alpha,
            "placement": dict(self.placement), "motion": dict(self.motion),
            "clips": [str(p) for p in self.clips],
            "clip": str(self.clip) if self.clip else None,
            "source": self.source,
        }


# ------------------------------------------------------------------ 内置主角
def builtin_hosts() -> dict:
    """开箱可用的主角。dsh-pet 素材缺了就返回空表，由调用方报错提示。"""
    hosts = {}
    if DEFAULT_MAID_DIR.is_dir():
        order = ["优雅女仆舞.webm", "东张西望.webm", "女仆屈膝礼仪.webm",
                 "凭空生花.webm", "三球抛接.webm"]
        clips = [DEFAULT_MAID_DIR / name for name in order
                 if (DEFAULT_MAID_DIR / name).is_file()]
        if clips:
            hosts["maid"] = HostProfile(
                id="maid", label="蓝毛小女仆（dsh-pet）", kind="avatar",
                voice=DEFAULT_VOICE, dir=DEFAULT_MAID_DIR, clips=clips,
                placement=dict(DEFAULT_PLACEMENT), source="builtin",
            )
    return hosts


# ------------------------------------------------------------------ 加载
def _resolve_path(value: str, base: Path) -> Path:
    p = Path(os.path.expanduser(str(value)))
    return p if p.is_absolute() else (base / p)


def _asset_alpha(path: Path) -> bool:
    """素材本身带不带透明通道。webm 按 VP9 alpha 惯例当有；png 真去看像素。"""
    suffix = path.suffix.lower()
    if suffix in (".webm", ".mov"):
        return True
    if suffix == ".png":
        try:
            from PIL import Image
            with Image.open(path) as img:
                if img.mode not in ("RGBA", "LA"):
                    return False
                return img.getchannel("A").getextrema()[0] < 255
        except Exception:  # noqa: BLE001 - 读不到就按不透明处理，宁可加相框
            return False
    return False


def _manifest_to_profile(manifest: dict, folder: Path, origin: str) -> HostProfile:
    kind = str(manifest.get("kind") or "avatar")
    if kind not in KINDS:
        raise HostError(f"{origin}: kind 只能是 {KINDS}，收到 {kind!r}")
    clips = [_resolve_path(c, folder) for c in (manifest.get("clips") or [])]
    clip = _resolve_path(manifest["clip"], folder) if manifest.get("clip") else None
    poster = _resolve_path(manifest["poster"], folder) if manifest.get("poster") else None
    if not clips and not clip and poster is not None:
        # 只有一张静态图也能当主角：渲染时转成带 alpha 的静帧视频
        clips = [poster]
    if not clips and not clip:
        raise HostError(f"{origin}: manifest 至少要有 clip、clips 或 poster")
    for p in clips + ([clip] if clip else []):
        if not p.is_file():
            raise HostError(f"{origin}: 素材不存在 {p}")
    placement = dict(DEFAULT_PLACEMENT)
    placement.update(manifest.get("placement") or {})
    motion = dict(DEFAULT_MOTION)
    motion.update(manifest.get("motion") or {})
    if clips and clip:
        clip = None
    elif clip and not clips:
        # 单个素材（静态图或视频片段）统一走 clips，avatar 也能轮播
        clips, clip = [clip], None
    if kind != "avatar":
        clips = clips[:1]
    return HostProfile(
        id=str(manifest.get("id") or folder.name),
        label=str(manifest.get("label") or folder.name),
        kind=kind,
        voice=str(manifest.get("voice") or DEFAULT_VOICE),
        dir=folder,
        clips=clips, clip=clip, poster=poster,
        # 没写 alpha 就按素材猜：透明 webm/png 直接贴，别的加圆角相框
        alpha=bool(manifest.get("alpha", _asset_alpha(clips[0] if clips else clip))),
        placement=placement, motion=motion,
        outline=bool(manifest.get("outline", True)),
        source=origin,
    )


def load_host(host: object = None, hosts_dir: Optional[Path] = None) -> HostProfile:
    """把 ``"maid"``、目录路径或 dict 统一变成 HostProfile。

    dict 形式支持在默认主角上做覆盖：``{"id": "maid", "voice": "...", "placement": {...}}``。
    """
    base = Path(hosts_dir) if hosts_dir else HOSTS_DIR
    builtin = builtin_hosts()

    if host is None or host == "":
        if DEFAULT_HOST_ID in builtin:
            return builtin[DEFAULT_HOST_ID]
        raise HostError(
            f"没有默认主角素材（{DEFAULT_MAID_DIR}）。请安装 dsh-pet 或用 --host 指定角色目录")

    override = {}
    if isinstance(host, dict):
        override = dict(host)
        host = override.pop("id", None) or override.pop("path", None) or DEFAULT_HOST_ID
    if isinstance(host, HostProfile):
        return host

    key = os.path.expanduser(str(host))
    candidate = Path(key)
    if candidate.is_dir():
        folder = candidate
    elif (base / key).is_dir():
        folder = base / key
    else:
        folder = None

    if folder:
        manifest_path = folder / "host.json"
        if not manifest_path.is_file():
            raise HostError(f"{folder} 缺少 host.json")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HostError(f"{manifest_path} 不是合法 JSON: {exc}") from exc
        manifest.setdefault("id", folder.name)
        profile = _manifest_to_profile(manifest, folder, origin=str(folder))
    elif key in builtin:
        profile = builtin[key]
    else:
        raise HostError(f"找不到主角 {key!r}：既不是内置 id，也不是带 host.json 的目录")

    if override:
        if "voice" in override:
            profile.voice = str(override["voice"])
        if "placement" in override:
            profile.placement = {**profile.placement, **override["placement"]}
        if "motion" in override:
            profile.motion = {**profile.motion, **override["motion"]}
        if "label" in override:
            profile.label = str(override["label"])
        if "alpha" in override:
            profile.alpha = bool(override["alpha"])
    return profile


def list_hosts(hosts_dir: Optional[Path] = None) -> list:
    """枚举可用主角，给面板下拉框用。"""
    base = Path(hosts_dir) if hosts_dir else HOSTS_DIR
    hosts = list(builtin_hosts().values())
    if base.is_dir():
        for folder in sorted(p for p in base.iterdir() if p.is_dir()):
            manifest_path = folder / "host.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest.setdefault("id", folder.name)
                hosts.append(_manifest_to_profile(manifest, folder, origin=str(folder)))
            except (HostError, json.JSONDecodeError, OSError):
                continue
    return hosts


# ------------------------------------------------------------------ 无 alpha 的相框
def make_frame(path: Path, width: int, height: int, pad: int = FRAME_PAD) -> Path:
    """给真人/数字人视频画一个圆角相框（RGBA PNG），垫在视频下面。"""
    from PIL import Image, ImageDraw
    w, h = int(width) + pad * 2, int(height) + pad * 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = max(18, min(w, h) // 14)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius,
                        fill=(14, 20, 40, 235), outline=(110, 168, 255, 120), width=3)
    d.rounded_rectangle([pad - 3, pad - 3, w - pad + 2, h - pad + 2],
                        radius=max(10, radius - 6), outline=(110, 168, 255, 60), width=1)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def probe_size(path: Path) -> tuple:
    """读素材宽高（像素），给相框和对齐用。"""
    proc = subprocess.run(
        [T.FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise HostError(f"探测素材尺寸失败: {path}: {proc.stderr.strip()[:200]}")
    try:
        stream = json.loads(proc.stdout)["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except (KeyError, IndexError, ValueError) as exc:
        raise HostError(f"素材没有视频流: {path}") from exc


def still_clip(png: Path, out: Path, seconds: float, fps: float = 30) -> Path:
    """静态头像 → 带 alpha 的静帧视频，供 overlay 当动画底。"""
    out = Path(out)
    proc = subprocess.run(
        [T.FFMPEG, "-y", "-v", "error", "-loop", "1", "-i", str(png),
         "-t", f"{max(0.2, float(seconds)):.3f}", "-r", str(fps),
         "-vf", "format=yuva420p", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
         "-auto-alt-ref", "0", "-b:v", "0", "-crf", "30",
         str(out)],
        capture_output=True, text=True, timeout=180)
    if proc.returncode != 0 or not out.is_file():
        raise HostError(f"静帧转视频失败: {png}: {proc.stderr.strip()[:200]}")
    return out


def ensure_clip(profile: HostProfile, work: Path, page_index: int,
                seconds: float, fps: float = 30) -> Optional[Path]:
    """取这一页该用的主角素材；只有静态图的主角顺带转成静帧视频。"""
    src = profile.clip_for(page_index)
    if src is None and profile.poster is not None:
        src = profile.poster
    if src is None:
        return None
    suffix = src.suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return src
    if suffix in (".png", ".webp", ".jpg", ".jpeg"):
        out = Path(work) / f"host{page_index:02d}.webm"
        return still_clip(src, out, seconds + 0.5, fps)
    raise HostError(f"不支持的主角素材类型: {src}")


def describe(profile: HostProfile) -> dict:
    return profile.to_dict()
