#!/usr/bin/env python3
"""Render local Creator Studio runtime config from the pinned upstream sources.

Reads the tracked upstream docker-compose.yml + app config.yaml files, and writes a
self-contained runtime tree under $STUDIO_HOME (default ~/CreatorStudio):

  config/compose.env          credentials referenced by the generated compose file
  config/compose.yml          full replacement compose (loopback only, no fixed names)
  config/aitoearn-server.yaml private server config (0600)
  config/aitoearn-ai.yaml     private ai config (0600)
  config/studio.json          non-secret local settings
  config/mpt/config.toml      MoneyPrinterTurbo config (0600)
  data/mpt projects logs backups

Idempotent: existing credentials in config/compose.env are reused. Never prints
secret values. Run with --check to only validate without writing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import stat
import string
import sys
from pathlib import Path

import yaml

# Upstream configs are written for a YAML 1.1 parser that keeps "9:16" as a
# string. PyYAML resolves sexagesimal integers (9:16 -> 556, 16:9 -> 969), which
# silently corrupted every aspect ratio and made aitoearn-ai refuse to boot.
# Load with the YAML 1.2 int resolver and emit every value as a quoted string so
# a round-trip cannot reintroduce the bug.
class _NoSexagesimalLoader(yaml.SafeLoader):
    pass


_NoSexagesimalLoader.yaml_implicit_resolvers = {
    ch: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:int"]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_NoSexagesimalLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    __import__("re").compile(r"^[-+]?(?:0|[1-9][0-9]*)$"),
    list("-+0123456789"),
)


class _QuotedStrDumper(yaml.SafeDumper):
    def represent_str(self, data):
        # Force quotes so a string that merely looks numeric (9:16, 1e5) stays a string.
        return self.represent_scalar("tag:yaml.org,2002:str", data, style='"')


_QuotedStrDumper.add_representer(str, _QuotedStrDumper.represent_str)


def load_yaml(text: str):
    return yaml.load(text, Loader=_NoSexagesimalLoader)


def dump_yaml(data) -> str:
    return yaml.dump(data, Dumper=_QuotedStrDumper, sort_keys=False, allow_unicode=True,
                     default_flow_style=False, width=1000)

REPO = Path(__file__).resolve().parent.parent
UPSTREAM_COMPOSE = REPO / "docker-compose.yml"
UPSTREAM_SERVER_CFG = REPO / "project/aitoearn-backend/apps/aitoearn-server/config/config.yaml"
UPSTREAM_AI_CFG = REPO / "project/aitoearn-backend/apps/aitoearn-ai/config/config.yaml"
MPT_VENDOR = REPO / "integrations/vendor/MoneyPrinterTurbo"

STUDIO_HOME = Path(os.environ.get("STUDIO_HOME", Path.home() / "CreatorStudio")).expanduser()
# Upstream MPT resolves $MPT_HOME/config.toml as its runtime config and task store.
MPT_HOME = Path(os.environ.get("MPT_HOME", STUDIO_HOME / "data/mpt")).expanduser()

# Loopback bindings (host:container)
PORTS = {
    "nginx": ["127.0.0.1:8080:80", "127.0.0.1:9000:9000"],
    "redis": ["127.0.0.1:6379:6379"],
    "rustfs": ["127.0.0.1:9001:9001"],
}

# Images are pinned locally; nothing is pulled during a normal `make up`.
IMAGES = {
    "mongodb": "${STUDIO_IMAGE_MONGO}",
    "mongodb-rs-init": "${STUDIO_IMAGE_MONGO}",
    "redis": "${STUDIO_IMAGE_REDIS}",
    "rustfs": "${STUDIO_IMAGE_RUSTFS}",
    "rustfs-init": "${STUDIO_IMAGE_MC}",
    "aitoearn-init": "${STUDIO_IMAGE_NODE}",
    "aitearn-ai": "${STUDIO_IMAGE_AI}",
    "aitoearn-ai": "${STUDIO_IMAGE_AI}",
    "aitoearn-server": "${STUDIO_IMAGE_SERVER}",
    "aitoearn-web": "${STUDIO_IMAGE_WEB}",
    "nginx": "${STUDIO_IMAGE_NGINX}",
}

DEFAULT_IMAGES = {
    "STUDIO_IMAGE_MONGO": "creator-studio/mongo:7.0",
    "STUDIO_IMAGE_REDIS": "creator-studio/redis:7-alpine",
    "STUDIO_IMAGE_RUSTFS": "creator-studio/rustfs:latest",
    "STUDIO_IMAGE_MC": "creator-studio/mc:latest",
    "STUDIO_IMAGE_NODE": "creator-studio/node:22-alpine",
    "STUDIO_IMAGE_AI": "aitoearn/aitoearn-ai:local",
    "STUDIO_IMAGE_SERVER": "creator-studio/aitoearn-server:${STUDIO_BUILD_TAG}",
    "STUDIO_IMAGE_WEB": "creator-studio/aitoearn-web:${STUDIO_BUILD_TAG}",
    "STUDIO_IMAGE_NGINX": "creator-studio/nginx:alpine",
}

ALNUM = string.ascii_letters + string.digits


def gen_password(n: int = 28) -> str:
    # Compose/YAML/shell safe alphabet: no quotes, $, :, @, /, #, spaces.
    alphabet = ALNUM + "-_.~"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def load_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()
    return data


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o600)


def ensure_credentials(env_path: Path) -> tuple[dict[str, str], bool]:
    existing = load_env_file(env_path)
    created = not env_path.exists()
    creds = {
        "STUDIO_MONGO_ROOT_USER": existing.get("STUDIO_MONGO_ROOT_USER", "aitoearn"),
        "STUDIO_MONGO_ROOT_PASSWORD": existing.get("STUDIO_MONGO_ROOT_PASSWORD") or gen_password(),
        "STUDIO_REDIS_PASSWORD": existing.get("STUDIO_REDIS_PASSWORD") or gen_password(),
        "STUDIO_S3_ACCESS_KEY": existing.get("STUDIO_S3_ACCESS_KEY") or ("cs" + gen_password(18)),
        "STUDIO_S3_SECRET_KEY": existing.get("STUDIO_S3_SECRET_KEY") or gen_password(40),
        "STUDIO_JWT_SECRET": existing.get("STUDIO_JWT_SECRET") or gen_password(48),
        "STUDIO_INTERNAL_TOKEN": existing.get("STUDIO_INTERNAL_TOKEN") or gen_password(40),
        "STUDIO_BUILD_TAG": existing.get("STUDIO_BUILD_TAG", "local"),
    }
    for k, v in DEFAULT_IMAGES.items():
        creds[k] = existing.get(k, v)
    creds["STUDIO_HOME"] = str(STUDIO_HOME)
    return creds, created


def env_text(creds: dict[str, str]) -> str:
    lines = [
        "# Generated by deploy/render_config.py -- do not commit. 0600.",
        "# Credentials for the local Creator Studio stack. Regenerate with `make setup --force-secrets`.",
        "",
    ]
    keys = [
        "STUDIO_HOME", "STUDIO_BUILD_TAG",
        "STUDIO_MONGO_ROOT_USER", "STUDIO_MONGO_ROOT_PASSWORD",
        "STUDIO_REDIS_PASSWORD",
        "STUDIO_S3_ACCESS_KEY", "STUDIO_S3_SECRET_KEY",
        "STUDIO_JWT_SECRET", "STUDIO_INTERNAL_TOKEN",
        "STUDIO_IMAGE_MONGO", "STUDIO_IMAGE_REDIS", "STUDIO_IMAGE_RUSTFS",
        "STUDIO_IMAGE_MC", "STUDIO_IMAGE_NODE", "STUDIO_IMAGE_AI",
        "STUDIO_IMAGE_SERVER", "STUDIO_IMAGE_WEB", "STUDIO_IMAGE_NGINX",
    ]
    for k in keys:
        lines.append(f"{k}={creds[k]}")
    lines.append("")
    return "\n".join(lines)


def mongo_uri(creds: dict[str, str]) -> str:
    u = creds["STUDIO_MONGO_ROOT_USER"]
    p = creds["STUDIO_MONGO_ROOT_PASSWORD"]
    return f"mongodb://{u}:{p}@mongodb:27017/?authSource=admin&directConnection=true"


def render_compose(creds: dict[str, str]) -> dict:
    """Take upstream compose as the structural base and emit a full replacement.

    - drops every fixed container_name / network name (compose project prefixes them)
    - replaces all published ports with loopback-only bindings
    - pins images and disables implicit pulls of our own builds
    - injects generated credentials
    - mounts the private config files instead of the git-tracked upstream ones
    """
    base = load_yaml(UPSTREAM_COMPOSE.read_text())
    services = base["services"]

    creds_env = {
        "MONGO_ROOT_USER": creds["STUDIO_MONGO_ROOT_USER"],
        "MONGO_ROOT_PASSWORD": creds["STUDIO_MONGO_ROOT_PASSWORD"],
    }
    mongo_uri_str = mongo_uri(creds)
    redis_pw = creds["STUDIO_REDIS_PASSWORD"]
    cfg_dir = STUDIO_HOME / "config"

    for name, svc in services.items():
        svc.pop("container_name", None)
        image = IMAGES.get(name)
        if image:
            svc["image"] = image
        # never let a normal up re-pull: everything is either built or pinned locally
        # （aitoearn-ai 也不该例外——本机 Docker Hub 不可达，"missing" 一样会打远端）
        svc["pull_policy"] = "never"
        if name in PORTS:
            svc["ports"] = PORTS[name]

    # --- mongodb: never published to the host ---
    mongo = services["mongodb"]
    mongo["ports"] = []
    mongo["environment"] = {
        "MONGO_INITDB_ROOT_USERNAME": creds_env["MONGO_ROOT_USER"],
        "MONGO_INITDB_ROOT_PASSWORD": creds_env["MONGO_ROOT_PASSWORD"],
    }
    mongo["healthcheck"]["test"] = [
        "CMD-SHELL",
        'mongosh "%s" --quiet --eval "try { quit(rs.status().ok === 1 ? 0 : 1) } catch(e) { quit(1) }"'
        % mongo_uri_str,
    ]
    services["mongodb-rs-init"]["entrypoint"] = [
        "/bin/sh",
        "-c",
        (
            'until mongosh "%s" --quiet --eval "db.adminCommand(\'ping\').ok" >/dev/null 2>&1; do sleep 2; done; '
            'mongosh "%s" --eval "try { rs.initiate({_id:\'rs0\', members:[{_id:0, host:\'mongodb:27017\'}]}) } '
            'catch(e) { if (e.codeName!==\'AlreadyInitialized\') throw e }"'
        )
        % (mongo_uri_str, mongo_uri_str),
    ]

    # --- redis: authenticated, PONG-checked healthcheck ---
    redis = services["redis"]
    redis["command"] = ["redis-server", "--requirepass", redis_pw, "--appendonly", "yes"]
    redis["environment"] = {"REDISCLI_AUTH": redis_pw}
    redis["healthcheck"] = {
        "test": ["CMD-SHELL", 'test "$$(redis-cli --raw ping)" = PONG'],
        "interval": "10s",
        "timeout": "3s",
        "retries": 5,
        "start_period": "30s",
    }

    # --- rustfs object store ---
    rustfs = services["rustfs"]
    rustfs["environment"] = {
        "RUSTFS_ACCESS_KEY": creds["STUDIO_S3_ACCESS_KEY"],
        "RUSTFS_SECRET_KEY": creds["STUDIO_S3_SECRET_KEY"],
    }
    # Upstream bootstraps the bucket with the `minio/mc` image. We do it from the
    # host instead (deploy/ensure_bucket.py, pure stdlib SigV4) so no extra image
    # is needed and bucket creation is idempotent.
    services.pop("rustfs-init", None)

    # --- init ---
    init = services["aitoearn-init"]
    init.setdefault("environment", {})
    init["environment"]["MONGO_URI"] = mongo_uri_str.rsplit("/?", 1)[0]
    init["environment"]["JWT_SECRET"] = creds["STUDIO_JWT_SECRET"]
    init["environment"]["DB_NAME"] = "aitoearn"
    init["environment"]["AUTO_LOGIN_TOKEN_PATH"] = "/data/init/token.txt"
    # init scripts live in the repo; mount absolutely so the runtime dir is self-contained
    init["volumes"] = [
        f"{REPO}/scripts/init.mjs:/app/init.mjs:ro",
        f"{REPO}/scripts/init-package.json:/app/package.json:ro",
        "init-data:/data/init",
    ]

    # --- ai / server: mount private configs ---
    services["aitoearn-ai"]["volumes"] = [f"{cfg_dir}/aitoearn-ai.yaml:/app/config.yaml:rw"]
    services["aitoearn-server"]["volumes"] = [f"{cfg_dir}/aitoearn-server.yaml:/app/config.yaml:rw"]

    # --- nginx config ---
    # Upstream nginx.conf addresses services by their container_name
    # (aitoearn-rustfs, ...). We deliberately drop fixed container names so two
    # stacks can coexist, which makes those hostnames unresolvable and nginx
    # dies with "host not found in upstream". Rewrite each container_name to the
    # compose service name and render our own copy.
    services["nginx"]["volumes"] = [f"{cfg_dir}/nginx.conf:/etc/nginx/nginx.conf:ro"]

    # network: unnamed so the compose project isolates it (no global collision)
    base["networks"] = {"aitoearn-network": {"driver": "bridge"}}
    return base


def render_nginx_config(compose: dict) -> str:
    """Rewrite the upstream nginx.conf so upstreams use compose service names.

    Upstream addresses services by container_name; we remove fixed container
    names (so two stacks can run side by side), so every such hostname has to be
    mapped back to the service that owns it. Any remaining container_name
    reference is reported loudly rather than shipped broken.
    """
    text = (REPO / "nginx/nginx.conf").read_text()
    services = compose.get("services") or {}

    # container_name -> service name, taken from the *upstream* compose file.
    upstream = load_yaml(UPSTREAM_COMPOSE.read_text()) or {}
    mapping: dict[str, str] = {}
    for svc, spec in (upstream.get("services") or {}).items():
        cname = (spec or {}).get("container_name")
        if cname and svc in services:
            mapping[cname] = svc

    for cname, svc in sorted(mapping.items()):
        # word-boundary replace so "aitoearn-rustfs-init" is not clobbered by
        # "aitoearn-rustfs".
        text = re.sub(rf"\b{re.escape(cname)}\b(?![\w-])", svc, text)

    leftover = sorted({c for c in mapping if re.search(rf"\b{re.escape(c)}\b", text)})
    if leftover:
        print(f"warning: nginx.conf still references {leftover}", file=sys.stderr)
    return text


def render_server_config(creds: dict[str, str]) -> dict:
    cfg = load_yaml(UPSTREAM_SERVER_CFG.read_text())
    cfg["auth"]["secret"] = creds["STUDIO_JWT_SECRET"]
    cfg["auth"]["internalToken"] = creds["STUDIO_INTERNAL_TOKEN"]
    cfg["redis"]["password"] = creds["STUDIO_REDIS_PASSWORD"]
    cfg["mongodb"]["uri"] = mongo_uri(creds)
    cfg["redlock"]["redis"]["password"] = creds["STUDIO_REDIS_PASSWORD"]
    if "channel" in cfg and "channelDb" in cfg.get("channel", {}):
        cfg["channel"]["channelDb"]["uri"] = mongo_uri(creds)
    assets = cfg.get("assets", {})
    assets["accessKeyId"] = creds["STUDIO_S3_ACCESS_KEY"]
    assets["secretAccessKey"] = creds["STUDIO_S3_SECRET_KEY"]
    assets["bucketName"] = "aitoearn"
    assets["endpoint"] = "http://rustfs.local:9000"
    assets["publicEndpoint"] = "http://localhost:9000"
    assets["cdnEndpoint"] = "http://localhost:8080/oss"
    # 服务端在容器内抓帧/探测：localhost:8080 指向容器自身，必须走内网 nginx。
    assets["internalCdnEndpoint"] = "http://nginx/oss"
    assets["forcePathStyle"] = True
    assets["provider"] = "s3"
    cfg["aiClient"]["token"] = creds["STUDIO_INTERNAL_TOKEN"]
    return cfg


def render_ai_config(creds: dict[str, str]) -> dict:
    cfg = load_yaml(UPSTREAM_AI_CFG.read_text())
    cfg["auth"]["secret"] = creds["STUDIO_JWT_SECRET"]
    cfg["auth"]["internalToken"] = creds["STUDIO_INTERNAL_TOKEN"]
    cfg["redis"]["password"] = creds["STUDIO_REDIS_PASSWORD"]
    cfg["mongodb"]["uri"] = mongo_uri(creds)
    cfg["redlock"]["redis"]["password"] = creds["STUDIO_REDIS_PASSWORD"]
    assets = cfg.get("assets", {})
    assets["accessKeyId"] = creds["STUDIO_S3_ACCESS_KEY"]
    assets["secretAccessKey"] = creds["STUDIO_S3_SECRET_KEY"]
    assets["endpoint"] = "http://rustfs.local:9000"
    assets["publicEndpoint"] = "http://localhost:9000"
    assets["cdnEndpoint"] = "http://localhost:8080/oss"
    assets["internalCdnEndpoint"] = "http://nginx/oss"
    return cfg


def _toml_set(text: str, section: str | None, key: str, value: str) -> str:
    """Set key=value inside [section] (or top level), preserving comments."""
    lines = text.splitlines()
    header = f"[{section}]" if section else None
    start = None
    end = len(lines)
    if header is None:
        start = 0
        # top level = everything before the first [section]
        for i, ln in enumerate(lines):
            if ln.strip().startswith("["):
                end = i
                break
    else:
        for i, ln in enumerate(lines):
            if ln.strip() == header:
                start = i + 1
                for j in range(i + 1, len(lines)):
                    if lines[j].strip().startswith("["):
                        end = j
                        break
                break
        if start is None:
            lines.extend(["", header])
            start = len(lines)
            end = len(lines)
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i in range(start, end):
        if pat.match(lines[i]):
            lines[i] = f"{key} = {value}"
            return "\n".join(lines) + "\n"
    lines.insert(end, f"{key} = {value}")
    return "\n".join(lines) + "\n"


def render_mpt_config(creds: dict[str, str]) -> Path | None:
    example = MPT_VENDOR / "config.example.toml"
    if not example.exists():
        return None
    text = example.read_text()
    text = _toml_set(text, None, "log_level", '"INFO"')
    text = _toml_set(text, None, "listen_host", '"127.0.0.1"')
    text = _toml_set(text, None, "listen_port", "8081")

    app = {
        "script_generation_backend": '"local"',
        "video_source": '"local"',
        "ffmpeg_path": json.dumps(shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"),
        "video_codec": '"h264_videotoolbox"',
        "material_concurrency": "1",
        "video_clip_concurrency": "1",
        "max_concurrent_tasks": "1",
        "upload_post_enabled": "false",
        "upload_post_auto_upload": "false",
        "enable_redis": "true",
        "redis_host": '"127.0.0.1"',
        "redis_port": "6379",
        "redis_db": "8",
        "redis_password": json.dumps(creds["STUDIO_REDIS_PASSWORD"]),
        "subtitle_provider": '"edge"',
    }
    for key, value in app.items():
        text = _toml_set(text, "app", key, value)
    text = _toml_set(text, "ui", "voice_name", '"zh-CN-XiaoxiaoNeural-Female"')

    # MPT reads $MPT_HOME/config.toml (see upstream app/config/config.py); MPT_HOME is
    # ~/CreatorStudio/data/mpt, so the runtime config must live there, not under config/.
    out = MPT_HOME / "config.toml"
    write_private(out, text)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="validate inputs, write nothing")
    ap.add_argument("--force-secrets", action="store_true", help="regenerate credentials")
    args = ap.parse_args()

    for p in (UPSTREAM_COMPOSE, UPSTREAM_SERVER_CFG, UPSTREAM_AI_CFG):
        if not p.exists():
            print(f"missing upstream file: {p}", file=sys.stderr)
            return 2

    cfg_dir = STUDIO_HOME / "config"
    env_path = cfg_dir / "compose.env"
    if args.force_secrets and env_path.exists():
        env_path.unlink()

    creds, created = ensure_credentials(env_path)

    if args.check:
        while True:  # validate the rendered docs parse
            compose = render_compose(creds)
            if dump_yaml(compose):
                break
        render_server_config(creds)
        render_ai_config(creds)
        print("render check ok")
        return 0

    for d in (
        cfg_dir, MPT_HOME, STUDIO_HOME / "data/sau",
        STUDIO_HOME / "projects", STUDIO_HOME / "logs", STUDIO_HOME / "backups",
        STUDIO_HOME / "data/sau",
    ):
        d.mkdir(parents=True, exist_ok=True)

    write_private(env_path, env_text(creds))
    write_private(cfg_dir / "compose.yml", dump_yaml(render_compose(creds)))
    write_private(cfg_dir / "aitoearn-server.yaml", dump_yaml(render_server_config(creds)))
    write_private(cfg_dir / "aitoearn-ai.yaml", dump_yaml(render_ai_config(creds)))
    write_private(cfg_dir / "nginx.conf", render_nginx_config(render_compose(creds)))

    local = {
        "studioHome": str(STUDIO_HOME),
        "repo": str(REPO),
        "workbenchUrl": "http://127.0.0.1:8080",
        "ports": {"web": 8080, "s3": 9001, "redis": 6379, "s3Proxy": 9000},
        "worker": {"pollIntervalSec": 3, "heartbeatSec": 10, "leaseSec": 60},
        "render": {"concurrency": 1, "nThreads": 2, "fps": 30, "width": 1080, "height": 1920},
        "publish": {"browserConcurrency": 1, "timezone": "Asia/Shanghai"},
        "features": {"remotion": False, "cloudVideoGeneration": False},
    }
    write_private(cfg_dir / "studio.json", json.dumps(local, indent=2, ensure_ascii=False) + "\n")

    mpt = render_mpt_config(creds)
    print(f"studio home     : {STUDIO_HOME}")
    print(f"credentials     : {'newly generated' if created else 'reused'}")
    print(f"compose         : {cfg_dir / 'compose.yml'}")
    print(f"server config   : {cfg_dir / 'aitoearn-server.yaml'}")
    print(f"ai config       : {cfg_dir / 'aitoearn-ai.yaml'}")
    print(f"mpt config      : {mpt or 'skipped (vendor checkout missing)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
