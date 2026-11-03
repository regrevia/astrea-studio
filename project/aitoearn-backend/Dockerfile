# Node 24 的 alpine 构建在本机 Docker VM 里启动即 SIGSEGV（node -e 也崩，
# 换 22 就正常），所以应用镜像统一钉在 22-alpine。
FROM node:22-alpine AS deps

RUN npm install -g pnpm

WORKDIR /app

COPY deps/ ./

# 不用 BuildKit 的 pnpm store 缓存：缓存坏了会把坏产物带进新镜像，
# 而这里每次构建本来就只装一次依赖。
RUN pnpm install --prod

FROM node:22-alpine AS production

ARG APP_NAME

RUN apk add --no-cache curl
RUN npm install -g pm2

WORKDIR /app

COPY assets/ ./
COPY --from=deps /app/ ./

COPY apps/ ./apps/
COPY libs/ ./libs/

ENV NODE_ENV=production
ENV APP_NAME=$APP_NAME

CMD ["sh", "-c", "exec pm2-runtime start apps/${APP_NAME}/src/main.js --name ${APP_NAME} -- -c config.yaml"]
