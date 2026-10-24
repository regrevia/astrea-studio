#!/usr/bin/env bash
# Install the pinned MoneyPrinterTurbo CLI environment.
#
# MPT requires Python >= 3.11 (the macOS system python3 is 3.9), so the venv is
# created by uv inside the pinned checkout. Nothing here writes outside the
# checkout or $MPT_HOME.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MPT_CHECKOUT="$REPO_ROOT/integrations/vendor/MoneyPrinterTurbo"
MPT_HOME="${MPT_HOME:-$HOME/CreatorStudio/data/mpt}"
PY_INDEX="${STUDIO_PYPI_INDEX:-https://pypi.tuna.tsinghua.edu.cn/simple}"

die() { echo "error: $*" >&2; exit 1; }

[ -f "$MPT_CHECKOUT/cli.py" ] || die "MPT checkout missing at $MPT_CHECKOUT"
command -v uv >/dev/null || die "uv not found; install it first (brew install uv)"

echo "MPT checkout : $MPT_CHECKOUT"
echo "MPT_HOME     : $MPT_HOME"
mkdir -p "$MPT_HOME"

if [ ! -x "$MPT_CHECKOUT/.venv/bin/python" ]; then
  echo "==> creating Python 3.11 venv"
  uv venv --python 3.11 "$MPT_CHECKOUT/.venv"
fi

echo "==> installing requirements"
UV_DEFAULT_INDEX="$PY_INDEX" uv pip install \
  --python "$MPT_CHECKOUT/.venv/bin/python" \
  -r "$MPT_CHECKOUT/requirements.txt"

echo "==> installing playwright chromium (subtitle/asset rendering helpers)"
"$MPT_CHECKOUT/.venv/bin/python" -m playwright install chromium 2>/dev/null || \
  echo "    skipped (playwright not required for the local-material path)"

echo "==> probing CLI"
MPT_HOME="$MPT_HOME" "$MPT_CHECKOUT/.venv/bin/python" "$MPT_CHECKOUT/cli.py" --help >/dev/null
echo "OK: MPT ready. config: $MPT_HOME/config.toml"
