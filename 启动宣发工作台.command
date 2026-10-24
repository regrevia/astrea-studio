#!/bin/bash
# 一键启动 Creator Studio 宣发工作台。
#
# 做三件事：
#   1. 同步宿主状态（cookie / 音色常量）到 ~/CreatorStudio/state
#   2. 起容器（若未运行）
#   3. 确保 worker 与能力服务在线，然后打开面板
#
# 参数：--no-open  不自动打开浏览器
set -uo pipefail

STUDIO_HOME="$HOME/CreatorStudio"
REPO="$HOME/Desktop/work/Studio/creator-studio"
URL="http://127.0.0.1:8080"

echo "=== Creator Studio 启动 ==="

# 1. 同步状态（launchd 读不到 Desktop，必须先镜像）
if [ -x "$STUDIO_HOME/runtime/integrations/mac-worker/sync_state.sh" ]; then
    echo "[1/4] 同步宿主状态…"
    "$STUDIO_HOME/runtime/integrations/mac-worker/sync_state.sh" > /dev/null 2>&1
else
    echo "[1/4] 跳过状态同步（脚本不存在）"
fi

# 2. 容器
echo "[2/4] 检查容器…"
UP=$(docker compose -p creatorstudio \
    --env-file "$STUDIO_HOME/config/compose.env" \
    -f "$STUDIO_HOME/config/compose.yml" ps --status running -q 2>/dev/null | wc -l | tr -d ' ')
if [ "$UP" -lt 7 ]; then
    echo "      启动容器（当前 $UP/7 运行中）…"
    docker compose -p creatorstudio \
        --env-file "$STUDIO_HOME/config/compose.env" \
        -f "$STUDIO_HOME/config/compose.yml" up -d > /dev/null 2>&1
else
    echo "      7 个容器已在运行"
fi

# 3. 后台服务
echo "[3/4] 检查后台服务…"
for svc in capabilities logindock worker; do
    if launchctl list | grep -q "local.creatorstudio.$svc"; then
        echo "      $svc 已加载"
    else
        launchctl load "$HOME/Library/LaunchAgents/local.creatorstudio.$svc.plist" 2>/dev/null \
            && echo "      $svc 已启动"
    fi
done

# 3b. 刷新 MCP 桥用的 user token（容器重建后 token 会变）
TOK=$(docker run --rm -v creatorstudio_init-data:/d creator-studio/node:22-alpine \
        cat /d/token.txt 2>/dev/null | tr -d '\r\n')
if [ -n "$TOK" ]; then
    printf '%s' "$TOK" > "$STUDIO_HOME/config/user.token"
    chmod 600 "$STUDIO_HOME/config/user.token"
    echo "      MCP token 已刷新"
fi

# 4. 等端口就绪再开浏览器
echo "[4/4] 等待面板就绪…"
for i in $(seq 1 40); do
    code=$(curl -s -o /dev/null -w "%{http_code}" -m 3 "$URL/" 2>/dev/null || echo 000)
    if [ "$code" = "200" ] || [ "$code" = "307" ]; then
        echo "      面板就绪：$URL"
        break
    fi
    sleep 1
done

if [ "${1:-}" != "--no-open" ]; then
    open "$URL/en/studio"
fi

echo "=== 完成 ==="
echo "面板：$URL/en/studio"
echo "扫码登录：http://127.0.0.1:8898/  ← 未登录的平台在这里点一下扫码"
echo "能力：$URL/en/studio 顶部「发布平台 / 配音音色」"
