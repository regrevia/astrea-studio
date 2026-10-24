/**
 * Studio 总览面板。
 *
 * 只做一件事：让用户在一屏内看清「任务现在到哪一步、成片进了哪个草稿箱、
 * 发布台账有没有说不清的状态」。新建任务用本页的时间轴任务表单（免手写 JSON），
 * 成片编辑走既有草稿箱页面。
 *
 * 状态语义（与后端台账一致）：
 *   queued/running  进行中，可取消
 *   succeeded       成片已进草稿箱
 *   failed          可重试
 *   submission_unknown  发布回执不明，绝不自动重投，需人工核对
 */

'use client'

import type { CharacterInfo, Inventory, ProductionRun, ProductionWorker, PublicationAttempt } from '@/api/production/production.types'
import { AlertCircle, CheckCircle2, Clock, Loader2, Mic, Plus, QrCode, RotateCw, Send, UserRound, Video } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { getCharacters, getHealth, getInventory, getProjects, getPublications, getRun, getRuns } from '@/api/production/production.api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { cn } from '@/utils/className'
import AssistantPanel from './components/AssistantPanel'
import FactCardForm from './components/FactCardForm'
import NewRunForm from './components/NewRunForm'
import { formatKib, PLATFORM_LABELS } from './studioConstants'

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
  // 各平台成片：后端按平台重新交付后写进 run.output.platforms，没有就是还没出片。
  const outputPlatforms = Object.entries(run.output?.platforms ?? {})

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

        {outputPlatforms.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {run.output?.videoUrl && (
              <a
                href={run.output.videoUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 rounded-md border bg-muted/40 px-2 py-0.5 text-[11px] hover:border-foreground/40"
              >
                <span className="font-medium">母版</span>
                <span className="text-muted-foreground">打开</span>
              </a>
            )}
            {outputPlatforms.map(([platform, video]) => {
              // 平台名 + 画幅 + 体积，点开就是该平台成片，省得再去草稿箱里翻。
              const label = (
                <>
                  <span className="font-medium">{PLATFORM_LABELS[platform] ?? platform}</span>
                  <span className="text-muted-foreground">
                    {video.width}
                    ×
                    {video.height}
                  </span>
                  <span className="text-muted-foreground">{formatKib(video.bytes)}</span>
                </>
              )
              const chipClass = 'inline-flex items-center gap-1 rounded-md border bg-muted/40 px-2 py-0.5 text-[11px]'

              return video.url
                ? (
                    <a
                      key={platform}
                      href={video.url}
                      target="_blank"
                      rel="noreferrer"
                      className={cn(chipClass, 'hover:border-foreground/40')}
                    >
                      {label}
                    </a>
                  )
                : <span key={platform} className={chipClass}>{label}</span>
            })}
          </div>
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
/** 主角（成片里负责解说的人）。虚拟形象、真人素材、数字人都走同一份清单。 */
const CHARACTER_KIND_LABELS: Record<string, string> = {
  avatar: '虚拟形象',
  person: '真人素材',
  digital_human: '数字人',
}

function PresenterPanel({ characters }: { characters: CharacterInfo[] }) {
  if (!characters.length) {
    return (
      <p className="text-xs text-muted-foreground">
        没读到主角清单。宿主机上的 capabilities server 没起来时这里会是空的；
        素材目录是
        {' '}
        <code className="text-[10px]">~/CreatorStudio/state/characters/&lt;id&gt;/</code>
        ，放一张透明 PNG 就能当主角。
      </p>
    )
  }
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        {characters.map(c => (
          <div key={c.id} className="min-w-[220px] rounded-lg border bg-muted/30 px-3 py-2">
            <div className="flex items-center gap-2">
              <UserRound className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="text-sm font-medium">{c.label}</span>
              <Badge variant="secondary">{CHARACTER_KIND_LABELS[c.kind] ?? c.kind}</Badge>
              {c.source === 'custom' && <Badge variant="outline">自定义</Badge>}
            </div>
            <div className="mt-1 text-xs text-muted-foreground">
              <code className="text-[10px]">{c.id}</code>
              {' · '}
              {c.clips}
              {' '}
              段素材
              {' · '}
              音色
              {' '}
              <code className="text-[10px]">{c.voice}</code>
            </div>
          </div>
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        主角自带音色，换主角就换解说。加新主角：建一个目录写
        {' '}
        <code className="text-[10px]">host.json</code>
        （见
        {' '}
        <code className="text-[10px]">integrations/mac-worker/export/CHARACTERS.md</code>
        ），刷新即出现。
      </p>
    </div>
  )
}

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
  const [characters, setCharacters] = useState<CharacterInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  /** 新建任务后需要单独追详情：列表接口只回概览，成片产物在 getRun 的 run 里。 */
  const [trackedIds, setTrackedIds] = useState<string[]>([])
  const [runDetails, setRunDetails] = useState<Record<string, ProductionRun>>({})

  const refresh = useCallback(async () => {
    try {
      // http.get<T> resolves the whole envelope ({code, data, message}), not the
      // payload — the project convention is `res?.data?.list`. Reading `.list`
      // straight off the envelope is undefined and silently renders empty cards.
      const [runsRes, pubsRes, healthRes, inv, chars] = await Promise.all([
        getRuns({ pageNo: 1, pageSize: 20 }),
        getPublications(),
        getHealth(),
        getInventory(),
        getCharacters(),
      ])
      setRuns(runsRes?.data?.list ?? [])
      setPublications(pubsRes?.data ?? [])
      setWorkers(healthRes?.data?.workers ?? [])
      setInventory(inv)
      setCharacters(chars)
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

  /**
   * 单独追「新建的任务」详情。列表接口只有概览，run.output.platforms 必须读 getRun；
   * 任务进入终态后停止追，产物留在 runDetails 里继续展示。
   */
  useEffect(() => {
    if (trackedIds.length === 0)
      return
    let cancelled = false

    const tick = async () => {
      const settled: string[] = []
      for (const id of trackedIds) {
        const res = await getRun(id)
        const detail = res?.data?.run
        if (!detail || cancelled)
          continue
        setRunDetails(prev => ({ ...prev, [id]: detail }))
        if (detail.status !== 'queued' && detail.status !== 'running')
          settled.push(id)
      }
      if (!cancelled && settled.length > 0)
        setTrackedIds(prev => prev.filter(id => !settled.includes(id)))
    }

    tick()
    const timer = setInterval(tick, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [trackedIds])

  /** 新建任务成功：接入详情轮询，并立刻刷新一次列表让其它卡片同步。 */
  const handleCreated = useCallback((runId: string) => {
    setTrackedIds(prev => (prev.includes(runId) ? prev : [...prev, runId]))
    void refresh()
  }, [refresh])

  /** 列表数据 + 详情数据合并：成片产物只存在于详情里，列表轮询不能把它冲掉。 */
  const mergedRuns = useMemo(() => {
    const list = runs.map((run) => {
      const detail = runDetails[run.id]
      return detail ? { ...run, ...detail } : run
    })
    const listed = new Set(runs.map(run => run.id))
    const fresh = Object.values(runDetails).filter(detail => !listed.has(detail.id))
    // 新任务排在列表最前：列表接口还没收录它时也能立刻看到。
    return [...fresh, ...list]
  }, [runs, runDetails])

  const active = useMemo(
    () => mergedRuns.filter(r => r.status === 'queued' || r.status === 'running'),
    [mergedRuns],
  )
  const finished = useMemo(
    () => mergedRuns.filter(r => r.status !== 'queued' && r.status !== 'running'),
    [mergedRuns],
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

      <AssistantPanel />

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
            <UserRound className="h-4 w-4" />
            主角
            <Badge variant="secondary">{characters.length}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <PresenterPanel characters={characters} />
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
            <Plus className="h-4 w-4" />
            新建时间轴任务
          </CardTitle>
        </CardHeader>
        <CardContent>
          <NewRunForm projects={projects} onCreated={handleCreated} />
        </CardContent>
      </Card>

      <FactCardForm projects={projects} onCreated={handleCreated} />

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
