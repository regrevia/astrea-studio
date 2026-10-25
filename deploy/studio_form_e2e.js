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
const COUNT = Number(process.argv[process.argv.indexOf('--count') + 1]) > 1
  ? Number(process.argv[process.argv.indexOf('--count') + 1]) : 1

function readToken() {
  const p = path.join(os.homedir(), 'CreatorStudio/config/user.token')
  return fs.readFileSync(p, 'utf8').trim()
}

;(async () => {
  // 机器上装了 Chrome 就不必再拉一份 Chromium：playwright-core + 系统浏览器
  // 也能无头跑，装包体积从几百 MB 降到几 MB。
  let pw
  try {
    pw = require('playwright')
  } catch {
    pw = require('playwright-core')
  }
  const launchOpts = { headless: true }
  try {
    await pw.chromium.launch({ ...launchOpts, channel: 'chrome' }).then((b) => b.close())
  } catch {
    launchOpts.executablePath = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
  }
  const { chromium } = pw
  const browser = await chromium.launch(launchOpts)
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
  await page.getByPlaceholder(/显存不够时/).fill('显存不够时，它替你续命')
  const pointBoxes = page.getByPlaceholder(/只存 K 和 V/)
  const n = await pointBoxes.count()
  // 卡面只落结论、口播另写一句：两处写一样的话，读卡就等于听完了。
  const lines = [
    '只留 K 和 V::Q 用完就扔',
    '记住换速度::越长越吃显存',
    '先铺缓存::prefill 先跑一遍',
  ]
  for (let i = 0; i < Math.min(n, lines.length); i += 1)
    await pointBoxes.nth(i).fill(lines[i])

  const narration = page.getByPlaceholder(/一行一屏/)
  if (await narration.count()) {
    await narration.fill([
      '先说结论。它不是技巧。',
      '它只记得 K 和 V，别的一概不留。',
      '代价很直白。缓存越长越占卡，你也躲不掉。',
      '所以先跑一遍 prefill，把缓存铺好再开跑。',
      '你选省显存，还是省时间？',
    ].join('\n'))
  }
  // 条数下拉：平台上的「一次出 2~3 条挑一条发」必须能在界面里点出来，
  // 只验接口等于没验到用户手上那一层。
  if (COUNT > 1) {
    await page.click('#fact-card-count')
    await page.getByRole('option', { name: String(COUNT), exact: true }).click()
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
    runId: parsed && parsed.data ? (parsed.data.runId || (parsed.data.runIds || [])[0]) : null,
    runIds: parsed && parsed.data ? (parsed.data.runIds || null) : null,
    count: COUNT,
    payload: posts.length ? JSON.parse(posts[0]) : null,
  }, null, 2))
  await browser.close()
})().catch((err) => {
  console.error('FAILED:', err && err.message)
  process.exit(1)
})
