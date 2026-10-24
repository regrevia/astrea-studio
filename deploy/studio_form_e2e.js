/**
 * 无头点击「一键出片」：注入登录态 → 打开 AI 指挥台 → 填表单 → 抓真实提交的请求体。
 * 全程 headless，不抢鼠标、不弹窗、不占主屏。
 * 用法：node deploy/studio_form_e2e.js [--submit]
 */
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const BASE = process.env.STUDIO_BASE || 'http://127.0.0.1:8080'
const SUBMIT = process.argv.includes('--submit')

function readToken() {
  const p = path.join(os.homedir(), 'CreatorStudio/config/user.token')
  return fs.readFileSync(p, 'utf8').trim()
}

;(async () => {
  const { chromium } = require('playwright')
  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1200 },
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
  })
  const token = readToken()
  await context.addInitScript((t) => {
    window.localStorage.setItem('User', JSON.stringify({
      state: { token: t, userInfo: {}, lang: 'zh-CN', hasEverLoggedIn: true, isAddAccountPorxy: false },
      version: 0,
    }))
  }, token)

  const page = await context.newPage()
  const posts = []
  page.on('request', (req) => {
    if (req.method() === 'POST' && req.url().includes('/api/production/runs')) {
      posts.push(req.postData() || '')
    }
  })
  page.on('pageerror', (e) => console.log('pageerror:', e.message))

  await page.goto(`${BASE}/zh-CN/studio`, { waitUntil: 'domcontentloaded', timeout: 45000 })
  const submit = page.getByRole('button', { name: '一键出片' })
  await submit.waitFor({ state: 'visible', timeout: 45000 })

  await page.getByPlaceholder('KV Cache 是什么').fill('KV Cache 是什么')
  await page.getByPlaceholder(/显存不够时/).fill('显存不够时，模型靠它续命')
  const pointBoxes = page.getByPlaceholder(/只存 K 和 V/)
  const n = await pointBoxes.count()
  const lines = [
    '只存 K 和 V::因为 Q 只在当前这一步用一次',
    '显存换速度::缓存越长越占卡，batch 越大越吃显存',
    '前置一步::推理框架会先跑一遍 prefill 把缓存铺好',
  ]
  for (let i = 0; i < Math.min(n, lines.length); i += 1)
    await pointBoxes.nth(i).fill(lines[i])

  const narration = page.getByPlaceholder(/一行一屏/)
  if (await narration.count()) {
    await narration.fill([
      '先说结论，KV Cache 不是优化技巧，是省时间的关键。',
      '它只存 K 和 V，因为 Q 只在当前这一步用一次。',
      '代价也很直白，缓存越长越占卡。',
      '所以推理框架会先跑一遍 prefill，把缓存铺好。',
      '想省显存还是想省时间，评论区说说你怎么选。',
    ].join('\n'))
  }
  await page.screenshot({ path: '/tmp/studio_form_filled.png', fullPage: true })

  if (!SUBMIT) {
    console.log(JSON.stringify({ filled: true, points: n, submitted: false }, null, 2))
    await browser.close()
    return
  }

  const respPromise = page
    .waitForResponse((r) => r.url().includes('/api/production/runs') && r.request().method() === 'POST', { timeout: 90000 })
    .catch(() => null)
  await submit.click()
  const resp = await respPromise
  let body = null
  if (resp) body = await resp.text().catch(() => null)
  await page.screenshot({ path: '/tmp/studio_form_after.png', fullPage: true })

  const parsed = body ? JSON.parse(body) : null
  console.log(JSON.stringify({
    submitted: true,
    http: resp ? resp.status() : null,
    runId: parsed && parsed.data ? parsed.data.runId : null,
    payload: posts.length ? JSON.parse(posts[0]) : null,
  }, null, 2))
  await browser.close()
})().catch((err) => {
  console.error('FAILED:', err && err.message)
  process.exit(1)
})
