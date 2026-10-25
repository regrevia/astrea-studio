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
  // 用系统 Chrome：playwright 自带的 headless shell 未必装着，下载一次几百 MB。
  // --use-mock-keychain：无头也会弹「找不到钥匙串」，弹窗会跑到用户屏幕上。
  const launchOpts = { headless: true, channel: 'chrome',
    args: ['--use-mock-keychain', '--disable-gpu'] }
  try {
    await pw.chromium.launch(launchOpts).then((b) => b.close())
  } catch {
    delete launchOpts.channel
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

  await page.goto(`${BASE}/zh-CN/studio`, { waitUntil: 'networkidle', timeout: 45000 })
  // 默认落在「AI 指挥台」会话页，出片表单收在「手动出片」后面
  const manual = page.getByRole('button', { name: '手动出片' })
  if (await manual.count()) await manual.first().click()
  const submit = page.getByRole('button', { name: '一键出片' })
  await submit.waitFor({ state: 'visible', timeout: 45000 })

  // 字段按 id 定位：占位符是提示文案，改一句文案脚本就断了
  await page.fill('#fact-card-topic', 'KV Cache 是什么')
  await page.fill('#fact-card-kicker', '显存不够时')
  await page.fill('#fact-card-hook', '显存不够时，它替你续命')
  await page.fill('#fact-card-narration', [
    '先说结论。它不是技巧。',
    '它只记得 K 和 V，别的一概不留。',
    '代价很直白。缓存越长越占卡，你也躲不掉。',
    '所以先跑一遍 prefill，把缓存铺好再开跑。',
    '你选省显存，还是省时间？',
  ].join('\n'))
  // 卡面只落结论、口播另写一句：两处写一样的话，读卡就等于听完了。
  const lines = [
    '只留 K 和 V::Q 用完就扔',
    '记住换速度::越长越吃显存',
    '先铺缓存::prefill 先跑一遍',
  ]
  for (let i = 0; i < lines.length; i += 1) {
    const box = page.locator(`#fact-card-point-${i}`)
    if (await box.count()) await box.fill(lines[i])
  }
  const n = lines.length
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
