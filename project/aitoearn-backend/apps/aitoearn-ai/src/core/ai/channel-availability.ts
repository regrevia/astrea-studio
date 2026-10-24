import { config } from '../../config'

/**
 * 渠道是否具备调用凭证。
 * 没有凭证的渠道一律视为不可用，价格与模型列表只下发能真正跑起来的模型。
 */
export function isChannelConfigured(channel: string): boolean {
  switch (channel) {
    case 'relay':
      return Boolean(config.ai.relay?.apiKey)
    case 'volcengine':
      return Boolean(
        config.ai.volcengine.apiKey
        || (config.ai.volcengine.accessKeyId && config.ai.volcengine.secretAccessKey),
      )
    case 'dashscope':
      return Boolean(config.ai.dashscope.apiKey)
    case 'gemini':
      return Boolean(config.ai.gemini.apiKey)
    case 'new-api':
      return Boolean(config.ai.openai.apiKey)
    case 'anthropic':
      return Boolean(config.ai.anthropic.apiKey)
    case 'grok':
      return Boolean(config.ai.grok.apiKey)
    default:
      return true
  }
}
