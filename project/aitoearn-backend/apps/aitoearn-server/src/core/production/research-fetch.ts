import { lookup } from 'node:dns/promises'
import { Injectable } from '@nestjs/common'
import { AppException, ResponseCode } from '@yikart/common'

/** 单页抓取超时：新闻站普遍 1s 内首字节，15s 足够，也不会把整轮对话拖死。 */
const FETCH_TIMEOUT_MS = 15_000
/** 响应体上限：正文再长也没意义，先卡住内存。 */
const MAX_BYTES = 3_000_000
/** 落库全文上限。 */
const MAX_CHARS = 8000
/** 回灌给模型的正文上限，模型上下文比磁盘贵得多。 */
const EXCERPT_CHARS = 2000
const MAX_REDIRECTS = 3

export interface ResearchPage {
  url: string
  title: string
  chars: number
  text: string
  excerpt: string
}

/** 是否是非公网地址：回环、内网、链路本地、组播、保留段全部拒绝。 */
export function isBlockedAddress(address: string): boolean {
  const ipv4 = address.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/)
  if (ipv4) {
    const [a, b, c] = [Number(ipv4[1]), Number(ipv4[2]), Number(ipv4[3])]
    if (a === 0 || a === 10 || a === 127 || a >= 224)
      return true
    if (a === 100 && b >= 64 && b <= 127)
      return true
    if (a === 169 && b === 254)
      return true
    if (a === 172 && b >= 16 && b <= 31)
      return true
    if (a === 192 && b === 168)
      return true
    if (a === 192 && b === 0 && (c === 0 || c === 2))
      return true
    if (a === 198 && (b === 18 || b === 19))
      return true
    if (a === 198 && b === 51 && c === 100)
      return true
    if (a === 203 && b === 0 && c === 113)
      return true
    return false
  }

  const value = address.toLowerCase().replace(/^\[|\]$/g, '')
  const mapped = value.match(/(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})$/)
  if (mapped) {
    return isBlockedAddress(mapped[1])
  }
  if (value === '::1' || value === '::')
    return true
  const head = Number.parseInt(value.split(':')[0] || '0', 16)
  if (Number.isNaN(head))
    return true
  if ((head & 0xFE00) === 0xFC00)
    return true // fc00::/7 唯一本地地址
  if ((head & 0xFFC0) === 0xFE80)
    return true // fe80::/10 链路本地
  if ((head & 0xFF00) === 0xFF00)
    return true // ff00::/8 组播
  return false
}

/** 解析并校验目标地址，返回可直接发请求的 URL。私网地址一律拒绝，防 SSRF。 */
async function assertPublicTarget(raw: string): Promise<URL> {
  let url: URL
  try {
    url = new URL(raw)
  }
  catch {
    throw new AppException(ResponseCode.ProductionAssistantResearchFailed, `链接格式不对：${raw}`)
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new AppException(ResponseCode.ProductionAssistantResearchFailed, '只支持 http/https 链接')
  }
  if (url.username || url.password) {
    throw new AppException(ResponseCode.ProductionAssistantResearchFailed, '链接不能带账号密码')
  }
  const hostname = url.hostname.replace(/^\[|\]$/g, '').toLowerCase()
  if (hostname === 'localhost' || hostname.endsWith('.localhost') || hostname.endsWith('.local')) {
    throw new AppException(ResponseCode.ProductionAssistantResearchFailed, `内网地址不允许访问：${hostname}`)
  }
  let addresses: string[]
  try {
    addresses = (await lookup(hostname, { all: true })).map(item => item.address)
  }
  catch {
    throw new AppException(ResponseCode.ProductionAssistantResearchFailed, `域名解析失败：${hostname}`)
  }
  if (!addresses.length) {
    throw new AppException(ResponseCode.ProductionAssistantResearchFailed, `域名没有可用地址：${hostname}`)
  }
  const blocked = addresses.find(item => isBlockedAddress(item))
  if (blocked) {
    throw new AppException(ResponseCode.ProductionAssistantResearchFailed, `内网地址不允许访问：${hostname} → ${blocked}`)
  }
  return url
}

function extractTitle(html: string): string {
  const matched = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)
  return (matched?.[1] ?? '').replace(/\s+/g, ' ').trim().slice(0, 120)
}

/** 粗暴但够用的正文抽取：去掉脚本/样式/标签，压缩空白。 */
export function htmlToText(html: string): string {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, ' ')
    .replace(/<svg[\s\S]*?<\/svg>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/g, '\'')
    .replace(/\s+/g, ' ')
    .trim()
}

/**
 * 联网调研：只抓公网页面的正文文本。
 *
 * 重定向要逐跳重新过 SSRF 校验——否则公网域名可以 302 到 127.0.0.1 绕过首跳检查。
 */
@Injectable()
export class ResearchFetcher {
  async fetch(raw: string): Promise<ResearchPage> {
    let target = await assertPublicTarget(raw)
    let response: Response | null = null
    for (let hop = 0; hop <= MAX_REDIRECTS; hop++) {
      try {
        response = await fetch(target, {
          redirect: 'manual',
          headers: {
            'user-agent': 'Mozilla/5.0 (compatible; StudioAssistant/1.0)',
            'accept': 'text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5',
          },
          signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
        })
      }
      catch (error) {
        throw new AppException(
          ResponseCode.ProductionAssistantResearchFailed,
          `抓取失败：${error instanceof Error ? error.message : String(error)}`,
        )
      }
      const location = response.headers.get('location')
      if (response.status >= 300 && response.status < 400 && location) {
        if (hop === MAX_REDIRECTS) {
          throw new AppException(ResponseCode.ProductionAssistantResearchFailed, '重定向次数过多')
        }
        target = await assertPublicTarget(new URL(location, target).toString())
        continue
      }
      break
    }
    if (!response) {
      throw new AppException(ResponseCode.ProductionAssistantResearchFailed, '抓取失败：没有响应')
    }
    if (!response.ok) {
      throw new AppException(ResponseCode.ProductionAssistantResearchFailed, `目标站返回 ${response.status}`)
    }
    if (Number(response.headers.get('content-length') ?? 0) > MAX_BYTES) {
      throw new AppException(ResponseCode.ProductionAssistantResearchFailed, '页面体积过大，已放弃抓取')
    }

    const html = (await response.text().catch(() => '')).slice(0, MAX_BYTES)
    const text = htmlToText(html).slice(0, MAX_CHARS)
    if (!text) {
      throw new AppException(ResponseCode.ProductionAssistantResearchFailed, '页面没有可读正文')
    }
    return {
      url: target.toString(),
      title: extractTitle(html) || new URL(target).hostname,
      chars: text.length,
      text,
      excerpt: text.slice(0, EXCERPT_CHARS),
    }
  }
}
