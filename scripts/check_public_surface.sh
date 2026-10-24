#!/usr/bin/env bash
# Public-surface gate: fails when a personal path, internal host, or credential
# shape sneaks into files we would publish. Run before any push to a public
# remote, and in CI once the split-out repos exist.
#
# Scope note: `project/` is the upstream AiToEarn fork and keeps its own rules,
# so it is excluded here. Only the pieces this workspace authored are checked.
set -u

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

fail=0
scan() {
  local label="$1" pattern="$2"
  local hits
  hits=$(git ls-files -z \
    | grep -zv '^project/' \
    | xargs -0 grep -InE "$pattern" 2>/dev/null \
    | grep -v 'scripts/check_public_surface.sh' \
    | head -20)
  if [ -n "$hits" ]; then
    echo "FAIL [$label]"
    echo "$hits" | sed 's/^/    /'
    fail=1
  else
    echo "ok   [$label]"
  fi
}

# 1) Someone's home directory in any form: a real username is a privacy leak in a
#    public diff even when the path itself is harmless.
scan "个人绝对路径" '/Users/[A-Za-z0-9._-]+/'
# 2) RFC1918 addresses and localhost-style service hosts. A public repo may show
#    127.0.0.1 (loopback is the documented default), never a LAN address.
scan "内网地址" '(^|[^0-9])(10\.[0-9]+\.[0-9]+\.[0-9]+|192\.168\.[0-9]+\.[0-9]+|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]+\.[0-9]+)'
# 3) Credential shapes: long bearer tokens, provider keys, and inline 32+ char
#    secrets assigned to a name that looks like a token.
scan "疑似凭证" '(sk-[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9._-]{20,}|(token|secret|password|api[_-]?key)["'"'"' ]*[:=]["'"'"' ]*[A-Za-z0-9._-]{24,})'

# 4) Files that must never be published regardless of content.
for bad in \
  "config/compose.env" "config/user.token" "config/worker.token" \
  "integrations/mac-worker/user.token" "integrations/mac-worker/worker.token" \
  "integrations/vendor" "docs/probe/export-panel/runs" \
  "integrations/mac-worker/export/.cache"; do
  if git ls-files --error-unmatch "$bad" >/dev/null 2>&1; then
    echo "FAIL [禁发文件] $bad 被 git 跟踪"
    fail=1
  fi
done
[ "$fail" -eq 0 ] && echo "ok   [禁发文件] 未被跟踪"

if [ "$fail" -ne 0 ]; then
  echo
  echo "公开面检查未通过：上面列出的内容不应出现在要开源的提交里。"
  exit 1
fi
echo
echo "公开面检查通过。"
