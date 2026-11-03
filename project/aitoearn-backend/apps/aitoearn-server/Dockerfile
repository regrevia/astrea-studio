# node 24 is not usable in this Docker VM: every invocation exits 139 (SIGSEGV),
# `node --version` included. Pin 22 — see RUNBOOK "Known constraints".
FROM node:22-alpine AS deps

RUN npm install -g pnpm

WORKDIR /app

COPY deps/ ./

RUN pnpm install --prod

FROM node:22-alpine AS production

ARG APP_NAME

# ffmpeg is a real runtime dependency: MaterialService extracts a draft cover
# from the rendered video. Without it the attach call fails and no draft is ever
# created — and `npm i -g ffmpeg` (the old line) only installs a download shim,
# which does NOT put an ffmpeg binary on PATH.
RUN apk add --no-cache curl ffmpeg
RUN npm install -g pm2

WORKDIR /app

COPY assets/ ./
COPY --from=deps /app/ ./

COPY apps/ ./apps/
COPY libs/ ./libs/

ENV NODE_ENV=production
ENV APP_NAME=$APP_NAME

CMD ["sh", "-c", "exec pm2-runtime start apps/${APP_NAME}/src/main.js --name ${APP_NAME} -- -c config.yaml"]
