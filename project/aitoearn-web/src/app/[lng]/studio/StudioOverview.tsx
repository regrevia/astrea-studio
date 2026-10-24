/**
 * Studio 总览面板。
 *
 * 只做一件事：让用户在一屏内看清「任务现在到哪一步、成片进了哪个草稿箱、
 * 发布台账有没有说不清的状态」。故意不做成第二套管理台——新建任务走生产
 * 任务的 createRun，成片编辑走既有草稿箱页面。
 *
 * 状态语义（与后端台账一致）：
 *   queued/running  进行中，可取消
 *   succeeded       成片已进草稿箱
 *   failed          可重试
 *   submission_unknown  发布回执不明，绝不自动重投，需人工核对
 */

'use client'

import type { Inventory, ProductionRun, ProductionWorker, PublicationAttempt } from '@/api/production/production.types'
import { AlertCircle, CheckCircle2, Clock, Loader2, Mic, QrCode, RotateCw, Send, Video } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { getHealth, getInventory, getProjects, getPublications, getRuns } from '@/api/production/production.api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { cn } from '@/utils/className'

/** 每 3 秒刷新一次；渲染本身就是分钟级，再快只是浪费请求。 */
const POLL_MS = 3000

/** 扫码登录坞。宿主上的独立小服务（8898），只做登录、不发布。 */
const LOGIN_DOCK = 'http://127.0.0.1:8898/'

const STATUS_META: Record<string, { label: string, tone: string, icon: typeof Clock }> = {
  queued: { label: '排队中', tone: 'bg-slate-100 text-slate-700', icon: Clock },
  running: { label: '渲染中', tone: 'bg-blue-100 text-blue-700', icon: Loader2 },
  succeeded: { label: '已完成', tone: 'bg-emerald-100 text-emerald-700', icon: CheckCircle2 },
  failed: { label: '失败', tone: 'bg-red-100 text-red-700', icon: AlertCircle },
  canceled: { label: '已取消', tone: 'bg-slate-100 text-slate-500', icon: AlertCircle },
}

function statusMeta(status: string) {
  return STATUS_META[status] ?? { label: status, tone: 'bg-slate-100 text-slate-700', icon: Clock }
}

function RunRow({ run, projectName }: { run: ProductionRun, projectName?: string }) {
  const meta = statusMeta(run.status)
  const Icon = meta.icon
  const busy = run.status === 'queued' || run.status === 'running'

  return (
    <div className="flex items-start gap-3 rounded-lg border p-3">
      <div className={cn('mt-0.5 rounded-md p-1.5', meta.tone)}>
        <Icon className={cn('h-4 w-4', run.status === 'running' && 'animate-spin')} />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate font-medium">{run.title}</span>
          <Badge variant="secondary" className={meta.tone}>{meta.label}</Badge>
          {projectName && (
            <span className="text-xs text-muted-foreground">{projectName}</span>
          )}
        </div>

        {busy && (
          <div className="mt-2 flex items-center gap-2">
            <Progress value={run.progress ?? 0} className="h-1.5 flex-1" />
            <span className="w-10 text-right text-xs tabular-nums text-muted-foreground">
              {run.progress ?? 0}
              %
            </span>
          </div>
        )}

        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span>阶段：{run.currentStage || '—'}</span>
          {run.revision ? <span>第 {run.revision} 版</span> : null}
          {run.duration ? <span>{run.duration.toFixed(1)}s</span> : null}
          {run.materialId ? <span className="text-emerald-600">已进草稿箱</span> : null}
        </div>

        {run.lastError && (
          <p className="mt-1 line-clamp-2 text-xs text-red-600">{run.lastError}</p>
        )}
      </div>
    </div>
  )
}

/**
 * 发布台账。这里刻意把 submission_unknown 单独高亮：它表示「我们不知道作品
 * 到底有没有发出去」，重投会造成重复发布，所以必须人工核对。
 */
function PublicationRow({ row }: { row: PublicationAttempt }) {
  const unknown = row.status === 'submission_unknown'
  return (
    <div className={cn('flex items-center justify-between rounded-lg border p-3', unknown && 'border-amber-300 bg-amber-50')}>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{row.platform}</span>
          <Badge variant="secondary">{row.status}</Badge>
        </div>
        <div className="mt-0.5 truncate text-xs text-muted-foreground">
          {row.accountName || row.accountRef || '未指定账号'}
          {row.workUrl ? ` · ${row.workUrl}` : ''}
        </div>
      </div>
      {unknown && (
        <span className="shrink-0 text-xs font-medium text-amber-700">
          回执不明，需人工核对
        </span>
      )}
    </div>
  )
}

/**
 * 平台矩阵。回答「我现在能在哪些平台发东西」。
 *
 * ready = cookies 目录下有该平台的 cookie 文件。这只是「登录过」，
 * 不代表 cookie 一定没过期 —— 真正的有效性校验要跑 SAU 的 check，
 * 会起浏览器，不适合放进面板轮询里。
 */
function PlatformMatrix({ inventory }: { inventory: Inventory | null }) {
  if (!inventory) {
    return (
      <p className="py-3 text-sm text-muted-foreground">
        能力服务未启动。在
        {' '}
        <code className="rounded bg-muted px-1">integrations/mac-worker</code>
        {' '}
        下运行
        {' '}
        <code className="rounded bg-muted px-1">python3 capabilities_server.py</code>
        {' '}
        即可看到平台与音色清单。
      </p>
    )
  }

  const { platforms, summary } = inventory
  const ready = platforms.filter(p => p.ready)
  const pending = platforms.length - ready.length

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>
          已接入
          {' '}
          <strong className="text-foreground">{summary.ready}</strong>
          {' '}
          /
          {' '}
          {summary.total}
          {' '}
          个平台
        </span>
        {ready.length === 0 && <span className="text-amber-600">还没有任何平台登录</span>}
        {pending > 0 && (
          <a
            href={LOGIN_DOCK}
            target="_blank"
            rel="noreferrer"
            className="ml-auto inline-flex items-center gap-1 rounded-md bg-foreground px-2.5 py-1 text-[11px] font-medium text-background hover:opacity-90"
          >
            <QrCode className="h-3 w-3" />
            扫码登录剩余
            {' '}
            {pending}
            {' '}
            个
          </a>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        {platforms.map((p) => {
          const main = p.accounts[0]
          // 未登录的平台可点击 → 直接跳到登录坞对应平台
          const card = (
            <div
              className={cn(
                'rounded-lg border p-2.5 text-xs transition',
                p.ready
                  ? 'border-emerald-200 bg-emerald-50'
                  : 'border-dashed hover:border-foreground/40 hover:bg-muted/40',
              )}
            >
              <div className="flex items-center justify-between">
                <span className={cn('font-medium', !p.ready && 'text-muted-foreground')}>{p.label}</span>
                {p.ready
                  ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
                  : <QrCode className="h-3.5 w-3.5 text-muted-foreground/60" />}
              </div>
              {main
                ? (
                    <div className="mt-1 truncate text-muted-foreground">
                      {main.account}
                      {main.hasNote ? ' · 视频+图文' : ' · 视频'}
                    </div>
                  )
                : <div className="mt-1 text-muted-foreground/60">点击扫码登录</div>}
            </div>
          )

          return p.ready
            ? <div key={p.platform}>{card}</div>
            : (
                <a
                  key={p.platform}
                  href={`${LOGIN_DOCK}#${p.platform}`}
                  target="_blank"
                  rel="noreferrer"
                  className="block"
                >
                  {card}
                </a>
              )
        })}
      </div>
    </div>
  )
}

/** 配音音色：剪映原生（sami:）+ Edge TTS。 */
function VoicePanel({ inventory }: { inventory: Inventory | null }) {
  const [tab, setTab] = useState<'jianying' | 'edge'>('jianying')
  if (!inventory) {
    return null
  }
  const list = tab === 'jianying' ? inventory.voices.jianying : inventory.voices.edge

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant={tab === 'jianying' ? 'default' : 'outline'}
          onClick={() => setTab('jianying')}
        >
          剪映原生
          {' '}
          <Badge variant="secondary" className="ml-1.5">{inventory.voices.jianying.length}</Badge>
        </Button>
        <Button
          size="sm"
          variant={tab === 'edge' ? 'default' : 'outline'}
          onClick={() => setTab('edge')}
        >
          Edge TTS
          {' '}
          <Badge variant="secondary" className="ml-1.5">{inventory.voices.edge.length}</Badge>
        </Button>
        <span className="text-xs text-muted-foreground">
          {tab === 'jianying' ? '解说小帅/猴哥/东北老铁等，0.5–2.6s 出片' : '云希/晓晓等，免费微软音色'}
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {list.map(v => (
          <span
            key={v.id}
            className="rounded-md border bg-muted/40 px-2 py-1 text-xs"
            title={v.voice ?? v.id}
          >
            {v.name}
            {' '}
            <code className="text-[10px] text-muted-foreground">{v.voice ?? v.id}</code>
          </span>
        ))}
      </div>
    </div>
  )
}

export default function StudioOverview() {
  const [runs, setRuns] = useState<ProductionRun[]>([])
  const [publications, setPublications] = useState<PublicationAttempt[]>([])
  const [workers, setWorkers] = useState<ProductionWorker[]>([])
  const [projects, setProjects] = useState<Record<string, string>>({})
  const [inventory, setInventory] = useState<Inventory | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      // http.get<T> resolves the whole envelope ({code, data, message}), not the
      // payload — the project convention is `res?.data?.list`. Reading `.list`
      // straight off the envelope is undefined and silently renders empty cards.
      const [runsRes, pubsRes, healthRes, inv] = await Promise.all([
        getRuns({ pageNo: 1, pageSize: 20 }),
        getPublications(),
        getHealth(),
        getInventory(),
      ])
      setRuns(runsRes?.data?.list ?? [])
      setPublications(pubsRes?.data ?? [])
      setWorkers(healthRes?.data?.workers ?? [])
      setInventory(inv)
      setError('')
    }
    catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    }
    finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    // Projects are static per session; fetch once and keep a name lookup.
    getProjects()
      .then((res) => {
        const map: Record<string, string> = {}
        for (const p of res?.data ?? [])
          map[p.id] = p.name
        setProjects(map)
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, POLL_MS)
    return () => clearInterval(timer)
  }, [refresh])

  const active = useMemo(
    () => runs.filter(r => r.status === 'queued' || r.status === 'running'),
    [runs],
  )
  const finished = useMemo(
    () => runs.filter(r => r.status !== 'queued' && r.status !== 'running'),
    [runs],
  )
  const onlineWorkers = workers.filter(w => w.online)
  const unknownCount = publications.filter(p => p.status === 'submission_unknown').length

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4 p-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Studio 总览</h1>
          <p className="text-sm text-muted-foreground">
            渲染与发布都在本机执行，数据只保存在这台机器上。
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={refresh} disabled={loading}>
          <RotateCw className={cn('mr-1.5 h-3.5 w-3.5', loading && 'animate-spin')} />
          刷新
        </Button>
      </div>

      {error && (
        <Card className="border-red-200 bg-red-50">
          <CardContent className="py-3 text-sm text-red-700">{error}</CardContent>
        </Card>
      )}

      {unknownCount > 0 && (
        <Card className="border-amber-300 bg-amber-50">
          <CardContent className="flex items-start gap-2 py-3 text-sm text-amber-800">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <div>
              有
              {' '}
              {unknownCount}
              {' '}
              条发布回执不明。它们不会被自动重投——先到对应平台确认作品是否存在，再到
              发布台账里回写结果，否则可能重复发布。
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <Send className="h-4 w-4" />
            发布平台
            {inventory && (
              <Badge variant="secondary">
                {inventory.summary.ready}
                /
                {inventory.summary.total}
              </Badge>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <PlatformMatrix inventory={inventory} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <Mic className="h-4 w-4" />
            配音音色
          </CardTitle>
        </CardHeader>
        <CardContent>
          <VoicePanel inventory={inventory} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <Video className="h-4 w-4" />
            进行中
            {active.length > 0 && <Badge variant="secondary">{active.length}</Badge>}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {active.length === 0
            ? (
                <p className="py-4 text-center text-sm text-muted-foreground">
                  没有正在渲染的任务。新建任务后，worker 会在几秒内领取。
                </p>
              )
            : active.map(run => (
                <RunRow key={run.id} run={run} projectName={projects[run.projectId]} />
              ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">最近完成</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {finished.length === 0
            ? <p className="py-4 text-center text-sm text-muted-foreground">还没有完成的任务。</p>
            : finished.slice(0, 8).map(run => (
                <RunRow key={run.id} run={run} projectName={projects[run.projectId]} />
              ))}
        </CardContent>
      </Card>

      {publications.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">发布台账</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {publications.slice(0, 8).map(row => <PublicationRow key={row.id} row={row} />)}
          </CardContent>
        </Card>
      )}

      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>执行器：</span>
        {workers.length === 0
          ? <span className="text-red-600">没有 worker 注册，任务不会被处理</span>
          : workers.map(w => (
              <Badge
                key={w.name}
                variant="secondary"
                className={w.online ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'}
              >
                {w.name}
                {' '}
                {w.online ? '在线' : '离线'}
              </Badge>
            ))}
        {onlineWorkers.length === 0 && workers.length > 0 && (
          <span className="text-red-600">全部离线，请检查 worker 是否在运行</span>
        )}
      </div>
    </div>
  )
}
