#!/bin/sh
# 解释器探测（shell 版）：先 `.python3` shim，再 MPT venv，最后才回退 PATH 的 python3。
#
# 本机 /usr/bin/python3 在同意 Xcode 许可前一律 exit 69，硬编码它等于让脚本在
# 终端能跑、在 launchd 子进程里必挂。用法两种：
#   . deploy/py3.sh && PY3=$(studio_py3)   # 被 source，只拿函数
#   PY=$(sh deploy/py3.sh)                 # 直接执行，打印选中的解释器
studio_py3() {
  if [ -n "${STUDIO_PYTHON3:-}" ] && "$STUDIO_PYTHON3" -c 'import sys' >/dev/null 2>&1; then
    printf '%s' "$STUDIO_PYTHON3"
    return 0
  fi
  _here=$(cd "$(dirname "$0")" 2>/dev/null && pwd || echo "$PWD")
  for _c in \
    "${HOME:-}/CreatorStudio/runtime/integrations/mac-worker/.python3" \
    "$_here/../integrations/mac-worker/.python3" \
    "${HOME:-}/CreatorStudio/runtime/integrations/vendor/MoneyPrinterTurbo/.venv/bin/python" \
    "$_here/../integrations/vendor/MoneyPrinterTurbo/.venv/bin/python"
  do
    [ -x "$_c" ] || continue
    if "$_c" -c 'import sys' >/dev/null 2>&1; then
      printf '%s' "$_c"
      return 0
    fi
  done
  # 都不可用时退回 PATH；真挂了调用方会看到 exit 69，但至少不是我们写死的路径
  printf 'python3'
}
if [ "${0##*/}" = "py3.sh" ]; then
  studio_py3
  printf '\n'
fi
