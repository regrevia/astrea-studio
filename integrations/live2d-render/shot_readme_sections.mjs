// 抓 README 的分段截图 + 仓库图标，供「固定卡片」版式使用。
// 用系统 Chrome（puppeteer-core），headless，不抢前台。
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import puppeteer from 'puppeteer-core';

const REPO = process.env.REPO ?? 'regrevia/envoy';
const OUT = process.env.OUT ?? '/tmp/ab/readme';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

await mkdir(OUT, { recursive: true });

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: [
    '--hide-scrollbars',
    '--force-device-scale-factor=2',
    '--font-render-hinting=none',
    '--disable-lcd-text',
    '--no-first-run',
    '--no-default-browser-check',
  ],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 1000, deviceScaleFactor: 2 });
await page.goto(`https://github.com/${REPO}`, { waitUntil: 'networkidle2', timeout: 90_000 });
await page.waitForSelector('article.markdown-body', { timeout: 30_000 });

// 图标 + 名字
const icon = await page.$('a[href="/' + REPO.split('/')[0] + '"] img.avatar, img.avatar');
if (icon) await icon.screenshot({ path: path.join(OUT, 'icon.png') });
const title = await page.$('#repo-title-component, strong[itemprop="name"]');
if (title) await title.screenshot({ path: path.join(OUT, 'title.png') });

// 每个 h2 段落：从标题顶部到下一个标题顶部
const sections = await page.evaluate(() => {
  const article = document.querySelector('article.markdown-body');
  if (!article) return [];
  const heads = [...article.querySelectorAll('h1, h2')];
  const top = article.getBoundingClientRect().top + window.scrollY;
  const box = article.getBoundingClientRect();
  return heads.map((h, i) => {
    const r = h.getBoundingClientRect();
    const next = heads[i + 1];
    const startY = r.top + window.scrollY;
    const endY = next
      ? next.getBoundingClientRect().top + window.scrollY
      : box.top + window.scrollY + box.height;
    return {
      text: h.textContent.trim().replace(/\s+/g, ' ').slice(0, 60),
      x: box.left + window.scrollX,
      y: startY,
      width: box.width,
      height: Math.max(80, endY - startY),
    };
  });
});

const index = [];
for (let i = 0; i < sections.length; i++) {
  const s = sections[i];
  const name = `sec${String(i).padStart(2, '0')}`;
  // 太高就截上限，避免超大图
  const clip = {
    x: Math.max(0, s.x),
    y: Math.max(0, s.y),
    width: Math.min(s.width, 1120),
    height: Math.min(s.height, 1800),
  };
  await page.screenshot({ path: path.join(OUT, `${name}.png`), clip });
  index.push({ name, heading: s.text, ...clip });
  console.log(name, `${Math.round(clip.width)}x${Math.round(clip.height)}`, s.text);
}

await writeFile(path.join(OUT, 'index.json'), JSON.stringify({ repo: REPO, sections: index }, null, 2));
await browser.close();
