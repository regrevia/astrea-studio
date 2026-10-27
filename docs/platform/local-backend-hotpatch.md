# 本机改后端代码怎么进容器

容器里的后端不是跑 TypeScript，而是编译后的 JS：`pm2-runtime start apps/aitoearn-server/src/main.js`。
所以改完 `project/aitoearn-backend` 的源码，只要把本机 `nx build` 的产物覆盖进容器同名目录、重启容器就够了，
不需要重建镜像。

适用场景：本机 `docker build` 拉基础镜像失败（TLS 报错）时，仍想让 8080 上跑的是最新代码。
如果镜像能正常重建，优先走 `docker compose build aitoearn-server`。

## 一条命令

```bash
scripts/dev/patch-backend-container.sh                 # 默认 creatorstudio-aitoearn-server-1 / aitoearn-server
scripts/dev/patch-backend-container.sh <容器名> <应用名>
```

脚本做四件事：

1. `nx build <应用>`，产物在 `dist/apps/<应用>/src`；
2. 把该目录打成 tar，容器里 `apps/<应用>/src` 是同一层结构（`main.js`、`core/…`、`views/`）；
3. `libs/*/src` 也一并覆盖——workspace 依赖在容器里靠 `node_modules/@yikart/*` 软链到 `/app/libs/*`，
   只换应用目录会出现「新代码调用旧 lib」；
4. `docker restart`，然后轮询 `http://127.0.0.1:8080/healthz` 直到 200（一般 10 s 内）。

## 注意

- 覆盖的是 JS 和 `.js.map`，容器里没有 TS 源码，别指望在容器里改 TS。
- 容器里的 `package.json`、`node_modules` 不动；新增 npm 依赖时热补丁不生效，必须重建镜像。
- 热补丁是**运行态**修改：`docker compose down` 或重建容器后回到镜像里的旧代码。
- 补完建议立刻验一条真实链路（例如让 AI 指挥台下一条 timeline 渲染单），确认新代码真的在跑。
- 脚本不写死任何机器路径，容器名/应用名都可用参数覆盖。
