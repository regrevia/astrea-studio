// 抓 GitHub 仓库真实界面（无头 Chrome，绝不抢前台）
// 用法: node shot_github.mjs regrevia/Envoy /tmp/ab/shots
import puppeteer from 'puppeteer-core'
import { mkdir } from 'node:fs/promises'

const [repo = 'regrevia/Envoy', outdir = '/tmp/ab/shots'] = process.argv.slice(2)
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

await mkdir(outdir, { recursive: true })

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--hide-scrollbars', '--force-device-scale-factor=2',
    '--font-render-hinting=none', '--disable-lcd-text'],
  defaultViewport: { width: 1440, height: 1000, deviceScaleFactor: 2 },
})

const page = await browser.newPage()
await page.goto(`https://github.com/${repo}`, { waitUntil: 'networkidle2', timeout: 60000 })
await page.waitForSelector('article.markdown-body', { timeout: 30000 })
await new Promise(r => setTimeout(r, 1200))

const shots = []
async function shot(name, handle) {
  if (!handle) return shots.push({ name, ok: false })
  const box = await handle.boundingBox()
  if (!box || box.width < 40 || box.height < 20) return shots.push({ name, ok: false, box })
  const path = `${outdir}/${name}.png`
  await handle.screenshot({ path })
  shots.push({ name, ok: true, w: Math.round(box.width), h: Math.round(box.height) })
}

// 1) 首屏（含仓库名、描述、star、语言）
await page.screenshot({ path: `${outdir}/00_firstscreen.png` })
shots.push({ name: '00_firstscreen', ok: true })

// 2) 单独元素
const pick = async (selectors, name) => {
  for (const sel of selectors) {
    const h = await page.$(sel)
    if (h) return shot(name, h)
  }
  shots.push({ name, ok: false })
}
await pick(['#repo-title-component', 'strong[itemprop="name"]', 'h1'], '01_reponame')
await pick(['p.f4.my-3', '[data-testid="repository-description"]',
  '.Layout-sidebar + div p', 'main p.f4'], '02_desc')
await pick(['[aria-labelledby="files"]', 'table[aria-labelledby="files"]',
  'div.Box.mb-3'], '03_files')
await pick(['.topic-tag:first-of-type', 'a.topic-tag'], '04_topic')
await pick(['article.markdown-body'], '05_readme')

// 3) README 里的代码块（安装命令最值钱）
const codes = await page.$$('article.markdown-body pre')
for (let i = 0; i < Math.min(codes.length, 4); i++) {
  await shot(`06_code${i}`, codes[i])
}

// 4) star / fork 区域
const stars = await page.$$eval('a[href$="/stargazers"], #repo-stars-counter-star', els =>
  els.map(e => ({ text: e.textContent.trim(), box: e.getBoundingClientRect().toJSON() })))
shots.push({ name: '07_stars_meta', stars })

console.log(JSON.stringify({ repo, shots }, null, 1))
await browser.close()
