# Creator Studio — RUNBOOK

Everything runs locally on this Mac. Nothing is published beyond `127.0.0.1`.

## What is actually running

| service | role | host port |
|---|---|---|
| `nginx` | single entry point: web UI, `/api`, S3 proxy | `127.0.0.1:8080`, `:9000` |
| `aitoearn-web` | Next.js workbench UI | internal 3000 |
| `aitoearn-server` | NestJS API (auth, production, publish) | internal 3002 |
| `aitoearn-ai` | AI service (Claude Code Router, task queues) | internal 3010 |
| `mongodb` | primary datastore (replica set, single node) | none |
| `redis` | queues, leases, pub/sub | `127.0.0.1:6379` |
| `rustfs` | S3-compatible object storage | `127.0.0.1:9001` (S3 API via `:9000`) |

Only nginx, redis and rustfs publish ports, and all three bind loopback. Container
names are *not* fixed, so a second stack can run beside this one.

## First run

```bash
cd ~/Desktop/work/Studio/creator-studio
make bootstrap          # render config + generate credentials into ~/CreatorStudio/config
make build              # backend images (aitoearn-server, aitoearn-ai)
make build-web          # Next.js image
make up
```

`make up` waits for health and prints the workbench URL.

## Daily use

```bash
make ps          # status
make health      # web / api / s3 probe
make logs S=aitoearn-server
make down        # stop, keep data
make up          # start again
```

`make health` 里 `web 307` 和 `s3 403` 都是正常的：307 是 i18n 把 `/`
重定向到语言前缀，403 是对象存储拒绝匿名列举。只有 `api` 必须是 200。

## Where things live

```
~/CreatorStudio/
  config/         compose.env (secrets), compose.yml, *.yaml, nginx.conf, studio.json
  data/           mongodb, redis, rustfs, sau session state, mpt tasks
  projects/       per-run working dirs
  logs/
  backups/
```

Secrets are generated once and reused. Deleting `config/compose.env` regenerates
everything — which also invalidates the local login, so don't do it casually.

## Known constraints on this machine

**The nx daemon wedges.** `node scripts/build-docker.mjs` hangs at 0% CPU with no
child processes. Always export `NX_DAEMON=false` first. `deploy/build_images.sh`
does this; if you build by hand, remember it.

**Docker Hub is flaky here.** Base images (`node:24-alpine`, `node:lts-alpine`)
frequently fail with `Head "https://registry-1.docker.io/...": EOF`. The images
are already in the local cache under `creator-studio/*`; the build scripts retag
them so builds never reach the network. If a base image is genuinely missing, pull
through the proxy on `127.0.0.1:7897`.

**`aitoearn-web` is not an nx project.** It is a separate Next.js repo with its own
Dockerfile. `deploy/build_images.sh aitoearn-web` fails; use
`deploy/build_web.sh` instead.

## Two bugs fixed in the upstream config path

`deploy/render_config.py` no longer uses `yaml.safe_dump`/`safe_load` directly,
because PyYAML implements YAML 1.1 and resolves sexagesimal integers:

- `9:16` → `556`, `16:9` → `969`. Every aspect ratio in `aitoearn-ai.yaml` was
  silently corrupted and the service refused to boot.
- The renderer now loads with a YAML 1.2 int resolver and quotes every string on
  output, so `"9:16"` stays a string through any round-trip.

nginx needed a second fix: upstream `nginx.conf` proxies to `container_name`
hostnames (`aitoearn-rustfs`). We removed fixed container names, so those hosts do
not resolve and nginx crash-loops with `host not found in upstream`. The renderer
rewrites each known `container_name` to its compose service name and writes
`config/nginx.conf`; a leftover reference is printed as a warning rather than
shipped broken.

## 封面抓帧：容器内不能用自己的 localhost

`assets.cdnEndpoint` 是对外地址（`http://localhost:8080/oss`），浏览器和数据库都用它。
但 server 容器里 `localhost:8080` 指的是**容器自己**，那里没人监听，于是 ffmpeg 抓帧
永远 `Connection refused`，草稿落库时 `coverUrl` 为空：

```
[tcp @ ...] Connection to tcp://localhost:8080 failed: Connection refused
Failed to extract video thumbnail for cover, proceeding without cover
```

抓帧失败是 `catch` 掉的 warn，不阻断出片，所以这个洞很安静——草稿能出，只是没封面。

修法是加一个**只给服务端进程用**的内部地址 `assets.internalCdnEndpoint=http://nginx/oss`。
`VideoMetadataService` 在调 `ffmpeg`/`ffprobe` 前把对外前缀换成内部前缀；写回数据库的
`coverUrl` 和返回给浏览器的 URL 仍然是对外地址，两个世界互不污染。

```bash
make config                       # 重新渲染出 internalCdnEndpoint
make build                        # 重建 server 镜像
make up
python3 deploy/p1_render_check.py --fixture ~/CreatorStudio/projects/_fixtures/fixture-02.mp4
```

验证新草稿 `coverUrl` 非空，并且 `curl http://127.0.0.1:8080/oss/<coverUrl>` 返回 200。

跨容器互相访问时记住这条：**配置里的 `localhost` 永远是那个进程自己的 localhost**。

## Codex 接 MCP

制作能力通过 MCP 暴露给 Codex，端点是 `http://127.0.0.1:8080/api/production/mcp`，
用 `x-api-key` 鉴权。库里只存 key 的 sha1，明文只在创建时返回一次。

网页端目前没有 API Key 管理界面，只能走接口创建（需要浏览器里已登录的 `access_token`）：

```bash
curl -s -X POST http://127.0.0.1:8080/api/api-key/create \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -d '{"name":"codex"}'
```

返回里的 `key` 就是明文，复制到下面配置里。

Codex 侧配置示例：

```toml
[mcp_servers.production]
url = "http://127.0.0.1:8080/api/production/mcp"
http_headers = { "x-api-key" = "<你的 key>" }
```

验证：

```bash
curl -s -X POST http://127.0.0.1:8080/api/production/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "x-api-key: $KEY" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 -m json.tool | head -30
```

应该看到 9 个 `*Production*` 工具。**没有 submit 工具是设计如此**——发布不可逆，
`planPublication` 只写台账，真正的上传必须由人或显式 API 触发。

## The worker (render + publish lanes)

One process serves both lanes: `render_mpt` (MPT CLI, CPU-bound, one job at a time)
and `publish_sau` (browser automation, strictly one browser). Two copies racing on
the same lane will both claim a lease and fight, so it must be a single supervised
process — not several `nohup` starts.

```bash
make worker-install     # install + start as a LaunchAgent (run in a real Terminal)
make worker-status      # loaded? pid? last exit code?
make worker-logs        # tail the same log the manual runs used
make worker-uninstall   # stop and remove
```

`launchctl bootstrap` 在部分受管环境里会失败，所以这一步最好在真实的 Terminal.app 里跑。
失败时它会把错误打出来，不会静默装一半。

**launchd 读不到 `~/Desktop`。** 这是本机最容易踩的坑：把 plist 直接指向仓库里的
`integrations/mac-worker/worker.py`，worker 会立刻退出（`last exit code = 2`），
stderr 只有一句 `Operation not permitted`。原因是 macOS TCC 不允许后台 agent
访问受保护的「桌面」目录，而交互式终端不受此限——所以手动跑一切正常，
装成 LaunchAgent 就崩，看起来像是"launchd 坏了"。

`make worker-install` 因此会先 `rsync` 一份 `integrations/` 到
`~/CreatorStudio/runtime/`，plist 指向那份副本。改了 worker 代码要重新
`make worker-install` 才生效（install 会重新同步）。诊断命令：

```bash
make worker-status                                   # state 应为 running
tail -5 ~/CreatorStudio/logs/worker-stderr.log       # 应为空
```

Without the LaunchAgent the worker still runs (`cd integrations/mac-worker &&
/usr/bin/python3 worker.py --verbose`), but it dies with the terminal session.
It is stdlib-only, so no venv is needed.

### Timeline render lane (`render_timeline`)

`do_timeline()` renders a master from our own timeline spec (ffmpeg + Pillow), and
when the spec lists `platforms` it also delivers one file per platform. It is a plain
background render — no GUI, no 剪映 — so it is safe to run while you keep working.

Two operational facts learned the hard way:

- **Claim filtering reads the DB row, not the worker process.** If
  `productionWorker.capabilities` lacks `render_timeline`, jobs sit in `queued`
  forever while the worker looks perfectly healthy. The worker now self-registers on
  boot and on idle (>600 s) via `POST /api/production/worker/capabilities`; against an
  older server image that endpoint 404s and the worker says so in its log.
- **A killed worker leaves a `running` job with an expired lease**, and the run then
  stays `queued` — the UI shows a zombie "进行中" row.
  `ProductionService.reapExpiredLeases()` fails the job and puts the run back to
  `queued` so a retry can pick it up.

## Backup and restore

```bash
# stop writes, then snapshot
make down
tar -czf ~/CreatorStudio-backup-$(date +%F).tgz -C ~ CreatorStudio/data

# restore
make down
rm -rf ~/CreatorStudio/data && tar -xzf ~/CreatorStudio-backup-YYYY-MM-DD.tgz -C ~
make up
```

`config/` is intentionally *not* in the data backup — it holds credentials that
should be rotated, not restored.

## When something is wrong

| symptom | cause | fix |
|---|---|---|
| `nginx` restarting, `host not found in upstream` | stale `config/nginx.conf` | `make config && make up` |
| `aitoearn-ai` exits at `selectConfig` | aspect ratio parsed as int | `make config` (regenerates with quoting) |
| `aitoearn-server` crash loop, `CannotDetermineTypeError` | a schema field uses an inferred union type | declare `@Prop({ type: Date })` explicitly |
| build hangs at 0% CPU | nx daemon | `export NX_DAEMON=false` |
| `failed to resolve source metadata ... EOF` | Docker Hub unreachable | retag from local cache, or pull via `:7897` |
| everything 502 | backend still booting | `make logs S=aitoearn-server`, wait for `app started at port` |
| publish fails `No module named 'conf'` | upstream ships only `conf.example.py` | `cp integrations/vendor/social-auto-upload/conf.example.py .../conf.py` (or rerun `deploy/bootstrap_sau.sh`) |
| publish says cookie expired | no logged-in Douyin session for that account | `cd integrations/vendor/social-auto-upload && .venv/bin/python sau_cli.py douyin login --account <name>` |
