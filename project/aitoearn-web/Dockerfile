FROM node:lts-alpine AS base
ENV NEXT_TELEMETRY_DISABLED=1
ENV NODE_ENV=production

RUN apk add --no-cache libc6-compat

RUN corepack enable pnpm && corepack prepare pnpm@latest --activate

WORKDIR /app

FROM base AS deps

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./

RUN pnpm install --frozen-lockfile --prod=false

FROM base AS builder
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# 不走 pnpm/corepack：corepack 每次都要去 registry 拉 pnpm 二进制，这台机器只有宿主代理，
# 构建容器经常拉不到。node_modules 已经在 deps 阶段装好，直接用里面的 next。
RUN ./node_modules/.bin/next build

FROM base AS production
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public

RUN mkdir .next
RUN chown nextjs:nodejs .next

COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]
