import { aitoearnAiClientConfigSchema } from '@yikart/aitoearn-ai-client'
import { aitoearnAuthConfigSchema } from '@yikart/aitoearn-auth'
import { assetsConfigSchema } from '@yikart/assets'
import { mongodbConfigSchema as channelDbConfigSchema } from '@yikart/channel-db'
import { baseConfig, createZodDto, selectConfig } from '@yikart/common'
import { mongodbConfigSchema } from '@yikart/mongodb'
import { redisConfigSchema } from '@yikart/redis'
import { redlockConfigSchema } from '@yikart/redlock'
import z from 'zod'
import { bilibiliConfigSchema } from './core/channels/platforms/bilibili/bilibili.config'
import { douyinConfigSchema } from './core/channels/platforms/douyin/douyin.config'
import { facebookConfigSchema } from './core/channels/platforms/facebook/facebook.config'
import { googleBusinessConfigSchema } from './core/channels/platforms/google-business/google-business.config'
import { instagramConfigSchema } from './core/channels/platforms/instagram/instagram.config'
import { kwaiConfigSchema } from './core/channels/platforms/kwai/kwai.config'
import { linkedinConfigSchema } from './core/channels/platforms/linkedin/linkedin.config'
import { pinterestConfigSchema } from './core/channels/platforms/pinterest/pinterest.config'
import { rednoteConfigSchema } from './core/channels/platforms/rednote/rednote.config'
import { threadsConfigSchema } from './core/channels/platforms/threads/threads.config'
import { tiktokConfigSchema } from './core/channels/platforms/tiktok/tiktok.config'
import { twitterConfigSchema } from './core/channels/platforms/twitter/twitter.config'
import { wechatConfigSchema } from './core/channels/platforms/wechat/wechat.config'
import { youtubeConfigSchema } from './core/channels/platforms/youtube/youtube.config'

const httpUrlSchema = z.url({ protocol: /^https?$/ })

export const relayConfigSchema = z.object({
  serverUrl: httpUrlSchema.describe('中转服务器地址'),
  apiKey: z.string().describe('用户 API Key'),
  callbackUrl: httpUrlSchema.describe('OAuth 回调完整地址，如 http://localhost:3000/api/v2/channels/relay/callback'),
})

export const apiKeyConfigSchema = z.object({
  prefix: z.string().min(1).default('ai_'),
}).default({ prefix: 'ai_' })

/**
 * Studio 指挥台的推理与宿主机能力来源。
 *
 * 推理走 OpenAI 兼容的 /chat/completions（容器内默认指向宿主机 Ollama），
 * 不依赖任何外部厂商 Key；inventoryUrl 指向 mac worker 的 capabilities server，
 * 用来回答「哪些平台账号、哪些音色可用」。两者都允许用环境变量覆盖：
 *   STUDIO_ASSISTANT_BASE_URL / STUDIO_ASSISTANT_MODEL / STUDIO_ASSISTANT_API_KEY
 *   STUDIO_ASSISTANT_INVENTORY_URL
 */
export const studioAssistantConfigSchema = z.object({
  baseUrl: httpUrlSchema.default('http://host.docker.internal:11434/v1').describe('OpenAI 兼容推理服务地址'),
  model: z.string().min(1).default('qwen3.5:9b').describe('模型名，需要支持原生 tools/tool_calls'),
  apiKey: z.string().default('').describe('可选 API Key，留空则不带鉴权头'),
  inventoryUrl: httpUrlSchema.default('http://host.docker.internal:8899').describe('宿主机能力清单地址'),
})

export const channelConfigSchema = z.object({
  channelDb: channelDbConfigSchema,
  shortLink: z.object({
    baseUrl: z.string().default(''),
  }),
  bilibili: bilibiliConfigSchema.optional(),
  douyin: douyinConfigSchema.optional(),
  facebook: facebookConfigSchema.optional(),
  kwai: kwaiConfigSchema.optional(),
  instagram: instagramConfigSchema.optional(),
  linkedin: linkedinConfigSchema.optional(),
  googleBusiness: googleBusinessConfigSchema.optional(),
  pinterest: pinterestConfigSchema.optional(),
  rednote: rednoteConfigSchema.optional(),
  threads: threadsConfigSchema.optional(),
  tiktok: tiktokConfigSchema.optional(),
  twitter: twitterConfigSchema.optional(),
  wechat: wechatConfigSchema.optional(),
  youtube: youtubeConfigSchema.optional(),
})

export const appConfigSchema = z.object({
  ...baseConfig.shape,
  environment: z.enum(['development', 'production']).default('development'),
  auth: aitoearnAuthConfigSchema,
  apiKey: apiKeyConfigSchema,
  redis: redisConfigSchema,
  mongodb: mongodbConfigSchema,
  redlock: redlockConfigSchema,
  assets: assetsConfigSchema,
  aiClient: aitoearnAiClientConfigSchema,
  channel: channelConfigSchema,
  relay: relayConfigSchema.optional(),
  studioAssistant: studioAssistantConfigSchema,
})

export class AppConfig extends createZodDto(appConfigSchema) { }

export const config = selectConfig(AppConfig)
