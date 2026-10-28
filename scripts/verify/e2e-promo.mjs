#!/usr/bin/env node
/**
 * 端到端验收：从平台接口起一条约 30 秒的竖屏片，等它出片，再拿母版本身核对规格。
 *
 * 只用平台已有的公开路径：`POST /api/production/runs` 建任务、`GET .../runs/:id` 轮询、
 * `output.videoUrl` 取母版。核对项是「成品能不能直接发」的硬指标：1080×1920、约 30 秒、
 * 有音轨。任何一项不达标就以非零码退出，避免把没验过的片子当验收产物。
 *
 * 用法：
 *   node scripts/verify/e2e-promo.mjs [--project <id>] [--tag <名字>] [--timeout <秒>]
 * token 默认读 /tmp/auto_token.txt，可用 E2E_TOKEN 覆盖。
 */
import { spawnSync } from 'node:child_process'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const BASE = process.env.E2E_BASE || 'http://127.0.0.1:8080'
const ROOT = resolve(import.meta.dirname, '../..')

function arg(name, fallback = '') {
  const i = process.argv.indexOf(`--${name}`)
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback
}

const projectId = arg('project', '6adc2161656a13996166bbfc')
const tag = arg('tag', 'promo')
const timeoutS = Number(arg('timeout', '900'))
const token = process.env.E2E_TOKEN || readFileSync('/tmp/auto_token.txt', 'utf8').trim()
const EVIDENCE_DIR = resolve(ROOT, 'docs/platform/evidence')
mkdirSync(EVIDENCE_DIR, { recursive: true })

const SCRIPT = [
  '一次改稿，一条片子::说一句改哪儿，几秒后新版本就在工作台里::写稿、出片、改稿都在一个页面完成',
  '素材自己长出来::卡面、立绘、配音和口型按稿子自动生成，不用手动对齐轨道::同一份稿子还能一次排几条',
  '改稿有据可查::每次修改都落成一个新版本，画面和文案都能对着看::不满意就再改一版，旧版本一直在',
  '导出和发布接着走::剪映工程一键生成，成片直接进草稿箱::想发的时候，片子已经在那儿等着了',
]

async function api(path, init = {}) {
  const res = await fetch(`${BASE}/api/production${path}`, {
    ...init,
    headers: {
      Accept: 'application/json',
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      ...(init.headers || {}),
    },
  })
  const text = await res.text()
  let body
  try {
    body = JSON.parse(text)
  } catch {
    throw new Error(`${path} 返回非 JSON（${res.status}）：${text.slice(0, 200)}`)
  }
  if (!res.ok || body.code !== 0)
    throw new Error(`${path} 失败（${res.status}/${body.code}）：${body.message || text.slice(0, 200)}`)
  return body.data
}

function ffprobe(file) {
  const probe = (entries) => spawnSync('ffprobe', [
    '-v', 'error', '-select_streams', entries, '-show_entries',
    'stream=width,height,codec_type,avg_frame_rate:format=duration', '-of', 'json', file,
  ], { encoding: 'utf8' })
  const stream = probe('v:0')
  const audio = probe('a:0')
  const parse = (r) => {
    if (r.status !== 0)
      throw new Error(`ffprobe 失败：${(r.stderr || '').slice(0, 200)}`)
    const parsed = JSON.parse(r.stdout || '{}')
    return { ...(parsed.streams?.[0] || {}), duration: Number(parsed.format?.duration || 0) }
  }
  return { video: parse(stream), audioCount: audio.status === 0 && audio.stdout.includes('codec_type') ? 1 : 0 }
}

function fail(reason, detail) {
  console.error(`验收失败：${reason}`)
  if (detail)
    console.error(JSON.stringify(detail, null, 2))
  writeFileSync(resolve(EVIDENCE_DIR, `e2e-${tag}-failed.json`),
    JSON.stringify({ reason, detail, at: new Date().toISOString() }, null, 2))
  process.exit(1)
}

const spec = {
  engine: 'timeline',
  mode: 'vertical_short',
  template: 'factcard',
  topic: '一体化宣发工作台',
  hook: '一条片子，从一句话到能发',
  points: SCRIPT,
  cta: '写稿、出片、改稿、发布，一处到底',
  voice: 'zh-CN-YunxiNeural',
  kicker: '宣发工作台',
}

console.log(`建任务：project=${projectId}`)
const created = await api('/runs', {
  method: 'POST',
  body: JSON.stringify({
    projectId,
    title: `E2E 验收片 ${new Date().toISOString().slice(0, 16)}`,
    brief: '端到端验收：30 秒竖屏宣传片',
    engine: 'timeline',
    template: 'factcard',
    spec,
  }),
})
const runId = created.runId
console.log(`runId=${runId} revision=${created.revision}`)

const started = Date.now()
let run
let jobs = []
const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'waiting_user'])
for (;;) {
  if ((Date.now() - started) / 1000 > timeoutS)
    fail(`轮询超时（${timeoutS}s）`, { runId, lastStatus: run?.status })
  await new Promise(r => setTimeout(r, 5000))
  const detail = await api(`/runs/${runId}`)
  run = detail.run
  jobs = detail.jobs ?? []
  process.stdout.write(`  [${Math.round((Date.now() - started) / 1000)}s] ${run.status} ${run.currentStage || ''}\n`)
  if (TERMINAL.has(run.status))
    break
}

if (run.status !== 'succeeded')
  fail(`任务没有出片：${run.status}`, { runId, lastError: run.lastError, output: run.output })

const videoUrl = run.output?.videoUrl
if (!videoUrl)
  fail('任务成功了却没有 videoUrl（没有真实产物不能算验收通过）', { runId, output: run.output })

const res = await fetch(`${BASE}${videoUrl}`)
if (!res.ok)
  fail(`母版下载失败：${videoUrl} → ${res.status}`, { runId })
const buf = Buffer.from(await res.arrayBuffer())
const local = `/tmp/e2e-${runId}.mp4`
writeFileSync(local, buf)

const probe = ffprobe(local)
const report = {
  at: new Date().toISOString(),
  runId,
  projectId,
  revision: run.revision,
  videoUrl,
  bytes: buf.length,
  width: probe.video.width,
  height: probe.video.height,
  fps: probe.video.avg_frame_rate,
  duration: Number(probe.video.duration.toFixed(3)),
  audio: probe.audioCount > 0,
  jobs: jobs.map(j => ({ kind: j.kind, attempt: j.attempt, status: j.status })),
}

const problems = []
if (probe.video.width !== 1080 || probe.video.height !== 1920)
  problems.push(`分辨率 ${probe.video.width}×${probe.video.height}，要求 1080×1920`)
if (!(report.duration >= 26 && report.duration <= 36))
  problems.push(`时长 ${report.duration}s，要求 26–36s（约 30 秒）`)
if (probe.audioCount === 0)
  problems.push('没有音轨')
if (problems.length)
  fail(problems.join('；'), report)

const out = resolve(EVIDENCE_DIR, `e2e-${tag}-${runId}.json`)
writeFileSync(out, JSON.stringify(report, null, 2))
console.log(`验收通过：1080×1920 / ${report.duration}s / ${report.fps} / 音轨=${report.audio}`)
console.log(`证据：${out}`)
