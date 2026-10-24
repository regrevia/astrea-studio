import { lookup } from 'node:dns/promises'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { htmlToText, isBlockedAddress, ResearchFetcher } from './research-fetch'

vi.mock('node:dns/promises', () => ({ lookup: vi.fn() }))

const PUBLIC_IP = { address: '93.184.216.34', family: 4 }

describe('researchFetcher', () => {
  const fetcher = new ResearchFetcher()
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.mocked(lookup).mockReset()
    vi.mocked(lookup).mockResolvedValue([PUBLIC_IP as never])
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  it('拒绝 localhost 与私网字面量，避免 SSRF', async () => {
    await expect(fetcher.fetch('http://localhost/admin')).rejects.toThrow('内网地址不允许访问')

    vi.mocked(lookup).mockResolvedValue([{ address: '127.0.0.1', family: 4 } as never])
    await expect(fetcher.fetch('http://evil.example.com/admin')).rejects.toThrow('内网地址不允许访问')

    vi.mocked(lookup).mockResolvedValue([{ address: '192.168.1.9', family: 4 } as never])
    await expect(fetcher.fetch('https://intranet.example.com/')).rejects.toThrow('内网地址不允许访问')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('非 http/https 与带账号密码的链接直接拒绝', async () => {
    await expect(fetcher.fetch('file:///etc/passwd')).rejects.toThrow('只支持 http/https')
    await expect(fetcher.fetch('https://user:pass@example.com/')).rejects.toThrow('链接不能带账号密码')
    await expect(fetcher.fetch('not a url')).rejects.toThrow('链接格式不对')
  })

  it('isBlockedAddress 认得出各类内网段', () => {
    for (const address of [
      '127.0.0.1',
      '10.1.2.3',
      '172.20.0.1',
      '192.168.1.1',
      '169.254.1.1',
      '100.64.0.1',
      '0.0.0.0',
      '::1',
      'fe80::1',
      'fd00::1',
      '::ffff:127.0.0.1',
    ]) {
      expect(isBlockedAddress(address)).toBe(true)
    }
    expect(isBlockedAddress('93.184.216.34')).toBe(false)
    expect(isBlockedAddress('172.15.0.1')).toBe(false)
    expect(isBlockedAddress('2606:2800:220:1:248:1893:25c8:1946')).toBe(false)
  })

  it('htmlToText 去掉脚本、样式与标签并保留正文', () => {
    const text = htmlToText(
      '<html><head><title>标题</title><style>.a{color:red}</style></head>'
      + '<body><script>alert(1)</script><h1>大标题</h1><p>第一段&nbsp;正文 &amp; 符号</p></body></html>',
    )

    expect(text).not.toContain('alert(1)')
    expect(text).not.toContain('color:red')
    expect(text).toContain('大标题')
    expect(text).toContain('第一段 正文 & 符号')
  })

  it('正文超过 8000 字截断，回灌摘要不超过 2000 字', async () => {
    fetchMock.mockResolvedValue(new Response(
      `<html><head><title>长文</title></head><body><p>${'字'.repeat(20_000)}</p></body></html>`,
      { status: 200, headers: { 'content-type': 'text/html' } },
    ))

    const page = await fetcher.fetch('https://example.com/long')

    expect(page.title).toBe('长文')
    expect(page.chars).toBe(8000)
    expect(page.text).toHaveLength(8000)
    expect(page.excerpt).toHaveLength(2000)
  })

  it('重定向逐跳重新校验，302 到私网会被拦下', async () => {
    fetchMock.mockResolvedValue(new Response(null, {
      status: 302,
      headers: { location: 'http://127.0.0.1:8080/internal' },
    }))
    vi.mocked(lookup)
      .mockResolvedValueOnce([PUBLIC_IP as never])
      .mockResolvedValueOnce([{ address: '127.0.0.1', family: 4 } as never])

    await expect(fetcher.fetch('https://example.com/go')).rejects.toThrow('内网地址不允许访问')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('目标站异常与空正文都变成可读中文错误', async () => {
    fetchMock.mockResolvedValueOnce(new Response('nope', { status: 404 }))
    await expect(fetcher.fetch('https://example.com/missing')).rejects.toThrow('目标站返回 404')

    fetchMock.mockResolvedValueOnce(new Response('<html><script>x</script></html>', { status: 200 }))
    await expect(fetcher.fetch('https://example.com/empty')).rejects.toThrow('页面没有可读正文')
  })

  it('页面体积过大时放弃抓取', async () => {
    fetchMock.mockResolvedValue(new Response('<p>hi</p>', {
      status: 200,
      headers: { 'content-length': String(4_000_000) },
    }))

    await expect(fetcher.fetch('https://example.com/huge')).rejects.toThrow('页面体积过大')
  })
})
