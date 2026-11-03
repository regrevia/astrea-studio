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
make s3-check    # 上传探针对象并三条路径读回（直连 / nginx /oss/ / presigned），用完即删
make logs S=aitoearn-server
make down        # stop, keep data
make up          # start again
```

`make health` 里 `web 307` 和 `s3 403` 都是正常的：307 是 i18n 把 `/`
重定向到语言前缀，403 是对象存储拒绝匿名列举。只有 `api` 必须是 200。

`make s3-check` 是比状态码更硬的一条：它真传一个对象、再按应用实际使用的三条路径读回来——
S3 直连（后端写）、`/oss/<key>`（服务端读，走 nginx 重写，靠桶策略匿名 GET）、
`http://localhost:9000/<bucket>/<key>`（后端签发的 presigned URL，客户端读）。三条都要
`200` 且内容一致，跑完删掉探针对象。改了对象存储或 nginx 之后跑它，别只看 `s3 403`。

## Where things live

```
~/CreatorStudio/
  config/         compose.env (secrets), compose.yml, *.yaml, nginx.conf, studio.json
  data/           mongodb, redis, rustfs, sau session state, mpt tasks
  projects/       per-run working dirs
  logs/
  backups/
  jianying/       Jianying drafts (kept on the internal disk on purpose)

/Volumes/US202/CreatorStudio/     external SSD, where renders go by default
  deliver/        finished films (GET /deliverables, bin/studio films)
  out/            films claimed back from Jianying
  work/           timeline / motion / slides / capture scratch, per job
```

Renders default to the external volume so exploration cannot fill the internal disk.
Paths are resolved on every use: if the volume is not mounted the worker falls back to
`~/CreatorStudio/{deliver,out,work}` and says so in the job result (`storage.fallbackReason`)
and in `GET /healthz`. Edit `config/studio.json` → `storage.root` to move it elsewhere;
`python3 integrations/mac-worker/storage.py` prints the current targets.

Secrets are generated once and reused. Deleting `config/compose.env` regenerates
everything — which also invalidates the local login, so don't do it casually.

## Known constraints on this machine

**The nx daemon wedges.** `node scripts/build-docker.mjs` hangs at 0% CPU with no
child processes. Always export `NX_DAEMON=false` first. `deploy/build_images.sh`
does this; if you build by hand, remember it.

**Docker Hub is flaky here.** Base images frequently fail with
`Head "https://registry-1.docker.io/...": EOF`. The images are already in the local
cache under `creator-studio/*`; the build scripts retag them so builds never reach
the network. If a base image is genuinely missing, pull through the proxy on
`127.0.0.1:7897`, e.g.
`docker pull docker.1ms.run/library/node:22-alpine && docker tag docker.1ms.run/library/node:22-alpine node:22-alpine`.

**`node:24-alpine` is unusable in this Docker VM.** Any invocation exits `139`
(SIGSEGV) — `docker run --rm node:24-alpine node -e 1` dies before printing
anything, `node --version` included, so it is not a build-time-only problem.
`node:22-alpine` (`v22.23.4`) is fine. Every Dockerfile here therefore pins 22, and
`deploy/build_images.sh` smoke-runs the base image before building so a bad base
fails in one line instead of 40 minutes into an `nx` build.

**`aitoearn-web` is not an nx project.** It is a separate Next.js repo with its own
Dockerfile. `deploy/build_images.sh aitoearn-web` fails; use
`deploy/build_web.sh` instead.

**The Docker Desktop image store can rot.** Observed 2026-11-03, after a doomed
`MemoryMiB` bump crashed the VM. Symptoms come in three flavours:

- a binary whose bytes are provably intact (`md5sum` stable across runs, ELF header
  `177 E L F`) fails to exec: `Error relocating /usr/local/bin/node: unsupported
  relocation type <a different number each run>`, or exit `139`;
- layer extraction fails: `archive/tar: invalid tar header`,
  `bash: : cannot allocate 32 bytes`, and on a fresh `docker pull` of a new tag:
  `failed to prepare extraction snapshot ... failed to stat parent: ... snapshots/<n>/fs: no such file or directory`;
- image metadata is gone: `docker image inspect <img> --format '{{.Os}}/{{.Architecture}}'`
  prints `/`, so compose refuses it with `image ... was found but does not provide
  any platform`. `DOCKER_DEFAULT_PLATFORM=linux/arm64` does not help — the daemon
  checks the stored manifest, not the default.

A clean Docker Desktop restart repairs part of it (cheap, try first: quit the app,
`pkill -f com.docker.backend`, `open -a Docker`, poll `docker info`). If
`docker save` also produces a truncated archive (`tar -tf` shows only `blobs/`,
no `oci-layout`/`index.json`) or `docker load` says `unrecognized image format`,
the containerd snapshot/content store itself is damaged and restarts will not fix it.

**Switching from the containerd image store to the classic one** is the
non-destructive repair. It is a one-key settings change, not a data wipe: volumes
live in the same VM and stay put, images do not — they must be re-imported, so make
a backup that is actually readable first.

```sh
# 1. back up the setting (rollback = put this file back, restart Docker)
mkdir -p ~/CreatorStudio/config/backup
cp "$HOME/Library/Group Containers/group.com.docker/settings-store.json" \
   ~/CreatorStudio/config/backup/docker-settings-$(date +%Y%m%d).json

# 2. back up local-only images. docker save is unreliable in a damaged store:
#    verify every archive (docker load must name the RepoTags) before going on.
docker save creator-studio/aitoearn-ai:local     -o /tmp/ai.tar
docker save creator-studio/aitoearn-server:local -o /tmp/server.tar
docker save creator-studio/aitoearn-web:local    -o /tmp/web.tar
docker load -i /tmp/ai.tar   # must print "Loaded image: ..."

# 3. flip the setting, restart Docker Desktop, wait for the daemon
#    ("UseContainerdSnapshotter": false in settings-store.json)
# 4. images from Docker Hub / the mirror must be re-pulled; local builds reload
docker load -i /tmp/ai.tar && docker load -i /tmp/server.tar && docker load -i /tmp/web.tar

# 5. check the stack's volumes survived before recreating anything
docker volume ls | grep creatorstudio_
```

Rollback: restore the JSON backup, restart Docker Desktop. Nothing in
`~/Library/Containers/com.docker.docker/Data` is deleted by this, and the
`Docker.raw` disk image is untouched.

Do **not** raise `MemoryMiB` above 4096 on this 24 GB host. The 8192 bump on
2026-11-03 crashed the VM (the daemon then reported `MemTotal` ≈ 3.8 GiB after the
revert) and is what started the store damage.

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
.python3 worker.py --verbose`), but it dies with the terminal session.
It is stdlib-only, so no venv is needed — but never launch it with
`/usr/bin/python3`: that interpreter exits 69 until the Xcode licence is accepted.
`.python3` is the shim that picks the MPT venv; `deploy/py3.sh` resolves the same
thing from shell scripts, and the worker writes the resolved path to the first line
of every job log.

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
| app container `Restarting (139)`, empty log; `--entrypoint node -v` works but `pm2 --version` segfaults | image payload was written while the image store was rotten — re-importing the rootfs does not heal it | rebuild: `NX_DAEMON=false bash deploy/build_images.sh aitoearn-ai aitoearn-server`, then `bash deploy/build_web.sh` |
| rebuild dies at `failed to fetch oauth token ... lookup host.docker.internal: no such host` | buildx cannot resolve the host alias after the store switch | give it the gateway IP, which needs no DNS: `STUDIO_BUILD_PROXY="http://$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}'):7897"` |
| rebuild ends with `DeadlineExceeded: context deadline exceeded` | the upstream helper caps a single build at a few minutes; a cold `pnpm install` through the proxy exceeds it | re-run once (cached layers get it under the cap) or raise the cap in `project/aitoearn-backend/scripts/build-docker.mjs` |
| job result says `storage.fallbackReason` / `GET /healthz` storage root is `~/CreatorStudio` | the external volume is not mounted or not writable | plug in the SSD and remount `/Volumes/US202`; new jobs route there by themselves, nothing to restart |
| everything 502 | backend still booting | `make logs S=aitoearn-server`, wait for `app started at port` |
| publish fails `No module named 'conf'` | upstream ships only `conf.example.py` | `cp integrations/vendor/social-auto-upload/conf.example.py .../conf.py` (or rerun `deploy/bootstrap_sau.sh`) |
| publish says cookie expired | no logged-in Douyin session for that account | `cd integrations/vendor/social-auto-upload && .venv/bin/python sau_cli.py douyin login --account <name>` |
