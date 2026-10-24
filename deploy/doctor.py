#!/usr/bin/env python3
"""Preflight / doctor for the local Creator Studio.

Reports what is missing without ever printing a credential value: secrets are
reported as configured/absent plus a length, never the string itself.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STUDIO_HOME = Path(os.environ.get("STUDIO_HOME", Path.home() / "CreatorStudio")).expanduser()
CONFIG = STUDIO_HOME / "config"
MPT = REPO / "integrations/vendor/MoneyPrinterTurbo"
SAU = REPO / "integrations/vendor/social-auto-upload"

LOOPBACK_PORTS = {8080: "web/nginx", 9000: "s3 proxy", 9001: "rustfs api", 6379: "redis", 27017: "mongodb"}
INTERNAL_PORTS = {3002: "server", 3010: "ai", 3000: "web"}

ok = True


def line(status: str, label: str, detail: str = "") -> None:
    global ok
    if status == "FAIL":
        ok = False
    print(f"[{status:4}] {label}" + (f" — {detail}" if detail else ""))


def env_values() -> dict[str, str]:
    out: dict[str, str] = {}
    path = CONFIG / "compose.env"
    if path.exists():
        for raw in path.read_text().splitlines():
            raw = raw.strip()
            if raw and not raw.startswith("#") and "=" in raw:
                k, v = raw.split("=", 1)
                out[k] = v
    return out


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def host_ports() -> set[int]:
    """Ports published on the host by this compose project."""
    try:
        out = subprocess.run(
            ["docker", "compose", "--env-file", str(CONFIG / "compose.env"),
             "-p", "creatorstudio", "-f", str(CONFIG / "compose.yml"),
             "ps", "--format", "{{.Ports}}"],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    ports: set[int] = set()
    for chunk in out.split():
        for part in chunk.split(","):
            part = part.strip()
            if "->" in part:
                left = part.split("->")[0]
                if ":" in left:
                    try:
                        ports.add(int(left.rsplit(":", 1)[1]))
                    except ValueError:
                        pass
    return ports


def main() -> int:
    print(f"repo        : {REPO}")
    print(f"studio home : {STUDIO_HOME}")
    print()

    # --- toolchain
    for tool, required in (("docker", True), ("node", True), ("pnpm", True),
                           ("uv", True), ("ffmpeg", True), ("ffprobe", True)):
        path = shutil.which(tool)
        line("PASS" if path else ("FAIL" if required else "WARN"), f"tool {tool}", path or "not found")

    try:
        info = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}} {{.Architecture}}"],
                              capture_output=True, text=True, timeout=20, check=False)
        line("PASS" if info.returncode == 0 else "FAIL", "docker daemon",
             info.stdout.strip() or info.stderr.strip()[:80])
    except (OSError, subprocess.SubprocessError) as exc:
        line("FAIL", "docker daemon", str(exc)[:80])

    # --- pinned sources
    for name, path, marker in (("AiToEarn", REPO / "project/aitoearn-backend", "package.json"),
                               ("MoneyPrinterTurbo", MPT, "cli.py"),
                               ("social-auto-upload", SAU, "sau_cli.py")):
        line("PASS" if (path / marker).exists() else "FAIL", f"source {name}", str(path))

    for name, path in (("MPT venv", MPT / ".venv/bin/python"), ("SAU venv", SAU / ".venv/bin/python")):
        line("PASS" if path.exists() else "WARN", name, str(path) if path.exists() else "run deploy/bootstrap_*.sh")

    # --- rendered config
    for name in ("compose.env", "compose.yml", "aitoearn-server.yaml", "aitoearn-ai.yaml", "studio.json"):
        p = CONFIG / name
        mode = oct(p.stat().st_mode & 0o777) if p.exists() else ""
        line("PASS" if p.exists() else "FAIL", f"config {name}",
             f"{mode}" if p.exists() else "run make config")
    mpt_cfg = STUDIO_HOME / "data/mpt/config.toml"
    line("PASS" if mpt_cfg.exists() else "FAIL", "config mpt/config.toml",
         oct(mpt_cfg.stat().st_mode & 0o777) if mpt_cfg.exists() else "run make config")

    env = env_values()
    for key in ("STUDIO_MONGO_ROOT_PASSWORD", "STUDIO_JWT_SECRET", "STUDIO_S3_ACCESS_KEY",
                "STUDIO_S3_SECRET_KEY", "STUDIO_REDIS_PASSWORD", "STUDIO_INTERNAL_TOKEN"):
        val = env.get(key, "")
        line("PASS" if val else "FAIL", f"secret {key}", f"{len(val)} chars" if val else "missing")

    token_file = CONFIG / "worker.token"
    line("PASS" if token_file.exists() and token_file.read_text().strip() else "WARN",
         "worker token", str(token_file) if token_file.exists() else "register a worker first")

    # --- revisions
    try:
        head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=15, check=False).stdout.strip()
        branch = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True, timeout=15, check=False).stdout.strip()
        line("PASS", "repo revision", f"{branch} @ {head}")
    except (OSError, subprocess.SubprocessError):
        line("WARN", "repo revision", "not a git worktree")

    for name, path, want in (("MPT", MPT, "6992380c5e82f07f3e1fc46b90927b6f1c655407"),
                             ("SAU", SAU, "28bb5bc20206c26d088311633540127402e4de56")):
        got = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=15, check=False).stdout.strip()
        line("PASS" if got == want else "WARN", f"{name} pinned SHA", (got or "unknown")[:12])

    # --- ports
    compose_text = (CONFIG / "compose.yml").read_text() if (CONFIG / "compose.yml").exists() else ""
    published: set[int] = set()
    public: list[str] = []
    try:
        import yaml
        doc = yaml.safe_load(compose_text) or {}
        for svc, spec in (doc.get("services") or {}).items():
            for entry in (spec.get("ports") or []):
                text = str(entry)
                parts = text.rsplit(":", 1)
                if len(parts) != 2:
                    continue
                host_part, _container = parts
                try:
                    published.add(int(host_part.rsplit(":", 1)[-1]))
                except ValueError:
                    continue
                # Anything not explicitly bound to 127.0.0.1 publishes on all interfaces.
                if not host_part.startswith("127.0.0.1:"):
                    public.append(f"{svc}:{text}")
    except ImportError:
        line("WARN", "yaml module", "pip install pyyaml for exact port parsing")

    line("PASS" if not public else "FAIL", "loopback-only bindings",
         "every published port is bound to 127.0.0.1" if not public else f"public: {public}")
    line("PASS" if "container_name" not in compose_text else "FAIL", "no fixed container names",
         "compose uses project-scoped names" if "container_name" not in compose_text else "container_name present")

    for port, why in LOOPBACK_PORTS.items():
        if port in published:
            line("PASS" if port_open(port) else "WARN", f"port {port} ({why})",
                 "127.0.0.1 reachable" if port_open(port) else "published but not answering")
        elif port == 27017:
            line("PASS", f"port {port} ({why})", "not published (compose-internal only)")
        else:
            line("WARN", f"port {port} ({why})", "not published yet")
    for port, why in INTERNAL_PORTS.items():
        if port in published:
            line("FAIL", f"port {port} leaked", f"{why} must stay compose-internal")
        else:
            line("PASS", f"port {port} ({why})", "compose-internal")

    # --- runtime state
    try:
        ps = subprocess.run(
            ["docker", "compose", "--env-file", str(CONFIG / "compose.env"),
             "-p", "creatorstudio", "-f", str(CONFIG / "compose.yml"),
             "ps", "--format", "{{.Service}}\t{{.State}}\t{{.Health}}"],
            capture_output=True, text=True, timeout=30, check=False).stdout
        for row in ps.strip().splitlines():
            svc, state, health = (row.split("\t") + ["", ""])[:3]
            line("PASS" if state == "running" else "WARN", f"service {svc}", f"{state} {health}".strip())
    except (OSError, subprocess.SubprocessError) as exc:
        line("WARN", "compose ps", str(exc)[:80])

    images = subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                            capture_output=True, text=True, timeout=20, check=False).stdout
    for img in ("creator-studio/aitoearn-server:local", "creator-studio/aitoearn-web:local",
                "aitoearn/aitoearn-ai:local"):
        line("PASS" if img in images else "WARN", f"image {img}",
             "present" if img in images else "run make build")

    print()
    print("doctor:", "OK" if ok else "ATTENTION NEEDED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
