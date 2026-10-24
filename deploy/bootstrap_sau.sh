#!/usr/bin/env bash
# Install the pinned social-auto-upload environment and its browser.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SAU_CHECKOUT="$REPO_ROOT/integrations/vendor/social-auto-upload"
PY_INDEX="${STUDIO_PYPI_INDEX:-https://pypi.tuna.tsinghua.edu.cn/simple}"

die() { echo "error: $*" >&2; exit 1; }

[ -f "$SAU_CHECKOUT/sau_cli.py" ] || die "SAU checkout missing at $SAU_CHECKOUT"
command -v uv >/dev/null || die "uv not found; install it first (brew install uv)"

echo "SAU checkout : $SAU_CHECKOUT"

if [ ! -x "$SAU_CHECKOUT/.venv/bin/python" ]; then
  echo "==> creating Python 3.11 venv"
  uv venv --python 3.11 "$SAU_CHECKOUT/.venv"
fi

# The CLI imports a local `conf` module that the upstream repo only ships as
# conf.example.py. Without it every command dies with "No module named 'conf'",
# so create it on first run but never overwrite an edited one (it holds paths and
# proxy settings a user may have changed).
if [ ! -f "$SAU_CHECKOUT/conf.py" ]; then
  echo "==> creating conf.py from conf.example.py"
  cp "$SAU_CHECKOUT/conf.example.py" "$SAU_CHECKOUT/conf.py"
fi

echo "==> installing package"
UV_DEFAULT_INDEX="$PY_INDEX" uv pip install \
  --python "$SAU_CHECKOUT/.venv/bin/python" -e "$SAU_CHECKOUT[web]"

echo "==> installing patchright chromium"
"$SAU_CHECKOUT/.venv/bin/python" -m patchright install chromium

echo "==> probing CLI"
"$SAU_CHECKOUT/.venv/bin/python" "$SAU_CHECKOUT/sau_cli.py" --help >/dev/null
echo "OK: SAU ready. Log in once per account:"
echo "    cd $SAU_CHECKOUT && .venv/bin/python sau_cli.py douyin login --account <name>"
