"""Where renders land.

成片和渲染中间件默认写到外置盘（``/Volumes/US202/CreatorStudio``），系统盘只留
代码、状态和日志——长时间探索不会把内置盘写满。

每条路径在**用的时候**才解析：外置盘没挂上就退回内置盘，并在任务结果里写明
实际落点和原因，而不是让任务失败。想改位置只改 ``config/studio.json`` 的
``storage`` 段，不用动代码：

```
{"storage": {"root": "/Volumes/US202/CreatorStudio",
             "out": "...", "deliver": "...", "work": "..."}}
```

优先级：环境变量（``STUDIO_OUT`` / ``DELIVER_DIR`` / ``STUDIO_STORAGE_ROOT``）
> ``studio.json`` > 外置盘默认值 > 内置盘兜底。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from config import CONFIG_DIR, STUDIO_HOME

DEFAULT_VOLUME = Path(os.environ.get("STUDIO_STORAGE_VOLUME", "/Volumes/US202")).expanduser()
DEFAULT_ROOT = DEFAULT_VOLUME / "CreatorStudio"
VOLUMES = Path("/Volumes")


@dataclass(frozen=True)
class Storage:
    root: Path
    out: Path
    deliver: Path
    work: Path
    drafts: Path
    fallback_reason: str | None = None

    def work_dir(self, kind: str, job_id: str) -> Path:
        path = self.work / kind / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def describe(self) -> dict:
        data = {"root": str(self.root), "out": str(self.out),
                "deliver": str(self.deliver), "work": str(self.work),
                "drafts": str(self.drafts)}
        if self.fallback_reason:
            data["fallbackReason"] = self.fallback_reason
        return data


def _settings() -> dict:
    try:
        data = json.loads((CONFIG_DIR / "studio.json").read_text())
    except (OSError, ValueError):
        return {}
    section = data.get("storage")
    return section if isinstance(section, dict) else {}


def _mount_point(root: Path) -> Path | None:
    """``/Volumes/X/...`` → ``/Volumes/X``；其它路径没有独立挂载点。"""
    if root.is_absolute() and root.parts[:2] == ("/", "Volumes") and len(root.parts) > 2:
        return VOLUMES / root.parts[2]
    return None


def _volume_ready(root: Path) -> tuple[bool, str]:
    mount = _mount_point(root)
    if mount is not None:
        # 盘拔掉后 /Volumes/X 目录可能残留成普通空目录，isdir 不够，要看挂载点
        if not mount.is_dir() or os.path.ismount(str(mount)) is False:
            return False, f"{mount} 未挂载"
        probe = mount
    else:
        probe = root
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        if not probe.exists():
            return False, f"{root} 不可用"
    if not os.access(probe, os.W_OK):
        return False, f"{mount or root} 不可写"
    try:
        # 真写一下：只读挂载、坏盘、配额满都在这露出
        if mount is not None:
            root.mkdir(parents=True, exist_ok=True)
        probe_file = (root if mount is not None else probe) / ".studio-write-probe"
        probe_file.write_text("ok")
        probe_file.unlink()
    except OSError as exc:
        return False, f"{mount or root} 写不进去：{exc.strerror or exc}"
    return True, ""


def resolve() -> Storage:
    settings = _settings()
    env_root = os.environ.get("STUDIO_STORAGE_ROOT")
    root = Path(str(env_root or settings.get("root") or DEFAULT_ROOT)).expanduser()
    internal = Path(str(settings.get("internalRoot") or STUDIO_HOME)).expanduser()
    ready, reason = _volume_ready(root)

    def pick(key: str, env: str, default: Path, fallback: Path) -> Path:
        explicit = os.environ.get(env) or settings.get(key)
        if explicit:
            return Path(str(explicit)).expanduser()
        return default if ready else fallback

    if ready:
        return Storage(
            root=root,
            out=pick("out", "STUDIO_OUT", root / "out", internal / "out"),
            deliver=pick("deliver", "DELIVER_DIR", root / "deliver", internal / "deliver"),
            work=pick("work", "STUDIO_WORK", root / "work", internal / "work"),
            drafts=pick("drafts", "STUDIO_DRAFTS", internal / "jianying", internal / "jianying"),
        )
    return Storage(
        root=internal,
        out=pick("out", "STUDIO_OUT", internal / "out", internal / "out"),
        deliver=pick("deliver", "DELIVER_DIR", internal / "deliver", internal / "deliver"),
        work=pick("work", "STUDIO_WORK", internal / "work", internal / "work"),
        drafts=pick("drafts", "STUDIO_DRAFTS", internal / "jianying", internal / "jianying"),
        fallback_reason=reason,
    )


def main(argv: list[str] | None = None) -> int:
    """``python3 storage.py``：面板和排障第一眼要看的就是「东西到底写到哪」。"""
    args = list(sys.argv[1:] if argv is None else argv)
    storage = resolve()
    if "--json" in args:
        print(json.dumps(storage.describe(), ensure_ascii=False, indent=2))
        return 0
    for key, path in storage.describe().items():
        if key == "fallbackReason":
            print(f"降级原因：{path}")
            continue
        exists = "存在" if Path(path).is_dir() else "未创建"
        print(f"{key:8s} {path}  ({exists})")
    return 0 if not storage.fallback_reason else 3


if __name__ == "__main__":
    raise SystemExit(main())
