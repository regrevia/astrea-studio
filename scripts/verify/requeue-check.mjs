/**
 * 活体验证：渲染 job 因本机环境问题失败时，run 自动回到 queued 并补发新 job。
 * 用真实 worker token 领取、真实 fail 接口上报，断言 job.requeued 事件落库。
 */
import { readFileSync } from 'node:fs'

const BASE = process.env.E2E_BASE || 'http://127.0.0.1:8080'
const token = readFileSync('/tmp/auto_token.txt', 'utf8').trim()
const wtoken = readFileSync(`${process.env.HOME}/CreatorStudio/config/worker.token`, 'utf8').trim()
const projectId = process.env.E2E_PROJECT || '6adc2161656a13996166bbfc'

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
  const body = JSON.parse(await res.text())
  if (!res.ok || body.code !== 0)
    throw new Error(`${path} ${res.status}/${body.code} ${body.message || JSON.stringify(body).slice(0, 200)}`)
  return body.data
}

const spec = {
  engine: 'timeline',
  mode: 'vertical_short',
  template: 'factcard',
  topic: '重排验证',
  hook: '一句',
  points: ['一::卡面一::口播一', '二::卡面二::口播二'],
  cta: '收尾',
  voice: 'zh-CN-YunxiNeural',
  kicker: '验证',
}
const created = await api('/runs', {
  method: 'POST',
  body: JSON.stringify({
    projectId,
    title: `重排活体验证 ${new Date().toISOString().slice(11, 19)}`,
    brief: '环境失败自动重排',
    engine: 'timeline',
    template: 'factcard',
    spec,
  }),
})
const runId = created.runId
console.log(`建任务 runId=${runId}`)

// 领取：worker 已停机，这里直接冒充 worker 领同一条 lane
let job
for (let i = 0; i < 40 && !job; i++) {
  const r = await fetch(`${BASE}/api/production/worker/claim`, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'x-worker-token': wtoken },
    body: JSON.stringify({ lane: 'render' }),
  })
  const body = JSON.parse(await r.text())
  if (!r.ok || body.code !== 0)
    throw new Error(`claim ${r.status}/${body.code} ${body.message || ''}`)
  job = (body.data ?? body).job
  if (!job)
    await new Promise(x => setTimeout(x, 1000))
}
if (!job)
  throw new Error('30 秒内没领到 job')
if (job.runId !== runId)
  throw new Error(`领到别的 run 的 job：${job.runId}`)
console.log(`领取 kind=${job.kind} keys=${Object.keys(job).join(',')} leaseVersion=${job.leaseVersion}`)
const claimedDetail = await api(`/runs/${runId}`)
const claimedJob = (claimedDetail.jobs ?? []).find(j => j.status === 'running' && j.kind === job.kind)
if (!claimedJob)
  throw new Error(`run 里找不到刚领的 job：${JSON.stringify(claimedDetail.jobs)}`)
const jobId = claimedJob.id
job.id = jobId
console.log(`领取 job=${jobId} attempt=${claimedJob.attempt}`)

const failed = await fetch(`${BASE}/api/production/worker/jobs/${job.id}/fail`, {
  method: 'POST',
  headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'x-worker-token': wtoken },
  body: JSON.stringify({ leaseVersion: job.leaseVersion, error: "ModuleNotFoundError: No module named 'fakemod_for_requeue_check'" }),
})
const failBody = JSON.parse(await failed.text())
if (!failed.ok)
  throw new Error(`fail ${failed.status} ${JSON.stringify(failBody).slice(0, 200)}`)

let detail
let jobs = []
for (let i = 0; i < 20; i++) {
  detail = await api(`/runs/${runId}`)
  jobs = detail.jobs ?? []
  if (detail.run.status === 'queued' && jobs.some(j => j.id !== job.id && j.status === 'queued'))
    break
  await new Promise(x => setTimeout(x, 1000))
}

const requeued = (detail.events ?? []).filter(e => e.kind === 'job.requeued')
const fresh = jobs.filter(j => j.id !== job.id && j.status === 'queued')
const report = {
  runId,
  oldJob: job.id,
  failResponse: failBody,
  runStatus: detail.run.status,
  runRevision: detail.run.revision,
  lastError: detail.run.lastError,
  jobs: jobs.map(j => ({ id: j.id, kind: j.kind, status: j.status, attempt: j.attempt })),
  queuedReplacement: fresh.map(j => j.id),
  requeuedEvents: requeued.map(e => ({ kind: e.kind, message: e.message, at: e.at })),
  pass: detail.run.status === 'queued' && fresh.length === 1 && requeued.length === 1,
}
console.log(JSON.stringify(report, null, 2))

// 清场：验证片不需要真渲，取消掉
await api(`/runs/${runId}/cancel`, { method: 'POST', body: '{}' }).catch(() => {})
process.exit(report.pass ? 0 : 1)
