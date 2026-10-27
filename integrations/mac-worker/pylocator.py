"""解释器定位：子进程一律先探 `.python3` shim，退到 MPT venv，最后才是当前解释器。

为什么需要这一层：本机 `/usr/bin/python3` 在同意 Xcode 许可前，任何调用都直接
exit 69（`You have not agreed to the Xcode license agreements`），而 launchd
默认 PATH 里只有 `/usr/bin`。硬编码 `/usr/bin/python3` 的脚本在终端能跑、在
worker 子进程里必挂，且报错只有一行 exit 69，看不出是谁拉起的。

探测顺序（第一个能跑通 `import sys` 的胜出）：
  1. STUDIO_PYTHON3 环境变量（人工救火用，优先级最高）
  2. ~/CreatorStudio/runtime/integrations/mac-worker/.python3（launchd 用的运行时 shim）
  3. <本仓库>/.python3（仓库里的同名 shim）
  4. MPT venv 的 python（shim 的执行目标，shim 坏了也能直接兜住）
  5. sys.executable（当前进程自己的解释器）

探测结果带来源，并且会被写进 job log 首行，出片失败时先看那一行就知道当时用的是
哪个解释器，不用再猜 PATH。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_STUDIO_HOME = Path(os.environ.get("STUDIO_HOME", Path.home() / "CreatorStudio")).expanduser()
_MPT_VENV_PY = (
    _STUDIO_HOME
    / "runtime/integrations/vendor/MoneyPrinterTurbo/.venv/bin/python"
)

_resolved: tuple[str, str] | None = None


def _candidates() -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    env = os.environ.get("STUDIO_PYTHON3", "").strip()
    if env:
        out.append((Path(env).expanduser(), "env:STUDIO_PYTHON3"))
    out.append((_STUDIO_HOME / "runtime/integrations/mac-worker/.python3", "shim:runtime"))
    out.append((_HERE / ".python3", "shim:repo"))
    out.append((_MPT_VENV_PY, "venv:mpt"))
    if sys.executable:
        out.append((Path(sys.executable), "sys.executable"))
    return out


def _works(path: Path) -> bool:
    try:
        if not path.is_file() or not os.access(path, os.X_OK):
            return False
        res = subprocess.run(
            [str(path), "-c", "import sys"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def resolve_python(refresh: bool = False) -> tuple[str, str]:
    """返回 (解释器绝对路径, 来源)。都探不到时退回 sys.executable，让子进程自己报错。"""
    global _resolved
    if _resolved is not None and not refresh:
        return _resolved
    for path, source in _candidates():
        if _works(path):
            _resolved = (str(path), source)
            return _resolved
    _resolved = (sys.executable or "python3", "fallback:no-candidate-worked")
    return _resolved


def describe() -> str:
    py, source = resolve_python()
    return f"interpreter={py} source={source} python={sys.version.split()[0]}"


def env_for_children() -> dict[str, str]:
    """给要 spawn 的子进程一份 PATH，保证它内部再找 `.python3`/venv 也找得到。"""
    env = dict(os.environ)
    extra = str(_STUDIO_HOME / "runtime/integrations/vendor/MoneyPrinterTurbo/.venv/bin")
    parts = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    if extra not in parts:
        parts.insert(0, extra)
    env["PATH"] = os.pathsep.join(parts)
    return env


def stamp_job_log(log_path: Path) -> None:
    """把解释器信息固定成 job log 首行；重试时保留已有首行，不重复刷。"""
    header = describe() + "\n"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists() and log_path.stat().st_size:
            body = log_path.read_text(encoding="utf-8", errors="replace")
            if body.startswith("interpreter="):
                return
            log_path.write_text(header + body, encoding="utf-8")
        else:
            log_path.write_text(header, encoding="utf-8")
    except OSError:
        # 日志写不进去不该让任务失败，渲染本身另有日志兜底
        pass


if __name__ == "__main__":
    print(describe())
