# 改完前端怎么在本机看到（不重建镜像）

镜像重建依赖 docker hub 与 alpine 软件源。网络受限时 `docker build` 会在
`RUN apk add --no-cache libc6-compat` 这一步直接失败（`TLS: unspecified error`），
而 `docker compose` 里的 `aitoearn-web` 只有 `image:`、没有 `build:`，所以容器里跑的
永远是上一次构建的前端——**前端改动提交了也看不见**。

绕开镜像的做法：在宿主机上构建同一份 Next 产物，用 standalone server 跑起来，
再让 nginx 的 `location /` 临时指向它。后端、AI、渲染队列都不用动。

## 一次性准备

```bash
cd project/aitoearn-web
NEXT_PUBLIC_API_URL=/api NEXT_PUBLIC_EVN=prod pnpm build
cp -R .next/static .next/standalone/.next/ && cp -R public .next/standalone/
```

`pnpm build` 走 `output: 'standalone'`，产物在 `.next/standalone/server.js`；
`static` 和 `public` 不在 standalone 目录里，必须手动拷进去，否则页面能开但样式和
图片全 404。

## 起服务

`AUTO_LOGIN_TOKEN` 是自动登录用的，容器从 `/data/init/token.txt` 读，本机直接取同一份：

```bash
cd project/aitoearn-web/.next/standalone
AUTO_LOGIN_TOKEN=$(docker exec aitoearn-web cat /data/init/token.txt) \
  PORT=3001 HOSTNAME=0.0.0.0 node server.js
```

`http://127.0.0.1:3001/zh-CN/studio` 已经是新界面，但它没有 nginx 的 `/api` 反代，
登录和接口会失败。要完整可用，把 nginx 的前端上游改成宿主机：

```bash
# nginx/nginx.conf 的 location / 里
proxy_pass http://host.docker.internal:3001;
```

```bash
docker exec creatorstudio-nginx-1 nginx -t && docker exec creatorstudio-nginx-1 nginx -s reload
```

然后 `http://127.0.0.1:8080/zh-CN/studio` 就是新界面，`/api/` 仍然走容器里的后端。
**这行改动不要提交**：它只对「本机临时验收」成立，别人机器上 3001 上没有进程。

## 语言路由

`/studio` 会被中间件重定向成 `<默认语言>/studio`，默认语言是 `en`，所以中文要用
`/zh-CN/studio`（不是 `/zh/`）。curl 验证时带 `Cookie: i18next=zh-CN` 才拿得到中文 SSR。

## 改完还原

```bash
git checkout nginx/nginx.conf && docker exec creatorstudio-nginx-1 nginx -s reload
```

镜像能重建的时候就回到镜像路线：`docker build --network=host -t
creator-studio/aitoearn-web:local project/aitoearn-web`，
`docker compose up -d --force-recreate aitoearn-web`。
