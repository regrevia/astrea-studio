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

import type { CharacterInfo, Inventory, ProductionRun, ProductionWorker, PublicationAttempt, RenderJob } from '@/api/production/production.types'
import { AlertCircle, CheckCircle2, ChevronDown, Loader2, Mic, Plus, QrCode, UserRound } from 'lucide-react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { apiGetMaterialGroupList } from '@/api/materials/material.api'
import { getCharacters, getHealth, getInventory, getProjects, getPublications, getRenderJobs, getRuns, updateProject } from '@/api/production/production.api'
import { useTransClient } from '@/app/i18n/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { cn } from '@/utils/className'
import AssistantPanel from './components/AssistantPanel'
import FactCardForm from './components/FactCardForm'
import NewRunForm from './components/NewRunForm'
import { QueueJobRow } from './components/QueueJobRow'
import RunDetailPanel from './components/RunDetailPanel'
import RunRow from './components/RunRow'
import { groupRuns, publicationStatusMeta } from './utils/status'

/** 每 3 秒刷新一次；渲染本身就是分钟级，再快只是浪费请求。 */
const POLL_MS = 3000

/** 扫码登录坞：独立小服务，只做登录、不发布。 */
const LOGIN_DOCK = 'http://127.0.0.1:8898/'

/** Radix Select 不接受空字符串值，用哨兵表示「全部项目」。 */
const ALL_PROJECTS = '__all__'

/** 草稿箱的「跟默认」也是一个空字符串，同样得用哨兵占住选项值。 */
const DEFAULT_DRAFT_GROUP = '__default__'

function PublicationRow({ row }: { row: PublicationAttempt }) {
  const { t } = useTransClient('studio')
  const meta = publicationStatusMeta(row.status)
  const unknown = row.status === 'submission_unknown'
  return (
    <div className={cn('flex items-center justify-between rounded-lg border p-3', unknown ? 'border-warning/50 bg-warning/10' : 'border-border')}>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{row.platform}</span>
          <Badge variant="secondary">{t(meta.labelKey)}</Badge>
        </div>
        <div className="mt-0.5 truncate text-xs text-muted-foreground">
          {row.accountName || row.accountRef || '-'}
          {row.workUrl ? ` · ${row.workUrl}` : ''}
        </div>
      </div>
      {unknown && (
        <span className="shrink-0 text-xs font-medium text-warning">
          {t('pub.manualCheck')}
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
      <p className="py-3 text-sm text-muted-foreground">平台清单还没读到，稍后刷新。</p>
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
        {ready.length === 0 && <span className="text-warning">还没有任何平台登录</span>}
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
                  ? 'border-success/30 bg-success/10'
                  : 'border-dashed hover:border-foreground/40 hover:bg-muted/40',
              )}
            >
              <div className="flex items-center justify-between">
                <span className={cn('font-medium', !p.ready && 'text-muted-foreground')}>{p.label}</span>
                {p.ready
                  ? <CheckCircle2 className="h-3.5 w-3.5 text-success" />
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
      <p className="text-xs text-muted-foreground">还没有主角素材。</p>
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
      <p className="text-xs text-muted-foreground">换主角就换解说。</p>
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
  const { t } = useTransClient('studio')
  const [runs, setRuns] = useState<ProductionRun[]>([])
  const [publications, setPublications] = useState<PublicationAttempt[]>([])
  const [workers, setWorkers] = useState<ProductionWorker[]>([])
  const [renderJobs, setRenderJobs] = useState<RenderJob[]>([])
  // 手动出片是给不想对话的人留的备用路径，默认收起，别让它挡住列表。
  const [showManual, setShowManual] = useState(false)
  const [projects, setProjects] = useState<Record<string, string>>({})
  /** 项目 → 草稿箱：留着给选择器回显当前绑定，不用每次再查一遍项目。 */
  const [projectGroups, setProjectGroups] = useState<Record<string, string>>({})
  const [draftGroups, setDraftGroups] = useState<{ id: string, name: string }[]>([])
  const [inventory, setInventory] = useState<Inventory | null>(null)
  const [characters, setCharacters] = useState<CharacterInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  /**
   * 选中态只有两个字段：projectId 决定列表范围，runId 决定右侧详情。
   * 两者都写进 URL，刷新同一地址能回到原来的项目和任务，聊天/表单/批量发表单
   * 都通过 setSelection 落到这同一份状态，不再各自维护一份。
   */
  const [projectId, setProjectId] = useState('')
  const [selectedRunId, setSelectedRunId] = useState('')

  /** 写回地址栏但不新增历史记录：切换项目/任务不该污染浏览器后退。 */
  const syncUrl = useCallback((next: { projectId?: string, runId?: string }) => {
    if (typeof window === 'undefined')
      return
    const url = new URL(window.location.href)
    if (next.projectId !== undefined) {
      if (next.projectId)
        url.searchParams.set('projectId', next.projectId)
      else
        url.searchParams.delete('projectId')
    }
    if (next.runId !== undefined) {
      if (next.runId)
        url.searchParams.set('runId', next.runId)
      else
        url.searchParams.delete('runId')
    }
    window.history.replaceState(null, '', `${url.pathname}${url.search}`)
  }, [])

  /** 首次挂载从地址栏恢复选中态（页面是 ssr:false，读 window 没有预渲染问题）。 */
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    setProjectId(params.get('projectId') ?? '')
    setSelectedRunId(params.get('runId') ?? '')
  }, [])

  const refresh = useCallback(async () => {
    try {
      // http.get<T> resolves the whole envelope ({code, data, message}), not the
      // payload — the project convention is `res?.data?.list`. Reading `.list`
      // straight off the envelope is undefined and silently renders empty cards.
      const [runsRes, pubsRes, healthRes, inv, chars, queue] = await Promise.all([
        getRuns({ pageNo: 1, pageSize: 30 }),
        getPublications(),
        getHealth(),
        getInventory(),
        getCharacters(),
        getRenderJobs(),
      ])
      setRuns(runsRes?.data?.list ?? [])
      setPublications(pubsRes?.data ?? [])
      setWorkers(healthRes?.data?.workers ?? [])
      setInventory(inv)
      setCharacters(chars)
      setRenderJobs(queue?.data ?? [])
      setError('')
    }
    catch (e) {
      setError(e instanceof Error ? e.message : t('overview.loadFailed'))
    }
    finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    // Projects are static per session; fetch once and keep a name lookup.
    getProjects()
      .then((res) => {
        const map: Record<string, string> = {}
        const groups: Record<string, string> = {}
        for (const p of res?.data ?? []) {
          map[p.id] = p.name
          groups[p.id] = p.materialGroupId ?? ''
        }
        setProjects(map)
        setProjectGroups(groups)
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    // 草稿箱清单：绑到项目上以后成片直接落这个箱子，列表不长，取一页够用。
    apiGetMaterialGroupList(1, 50)
      .then((res) => {
        const list = res?.data?.list ?? []
        setDraftGroups(list.map(group => ({ id: group.id, name: group.name })))
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, POLL_MS)
    return () => clearInterval(timer)
  }, [refresh])

  /** 项目范围：后端列表不按项目过滤，这里按 run.projectId 收窄。 */
  const visibleRuns = useMemo(
    () => (projectId ? runs.filter(run => run.projectId === projectId) : runs),
    [runs, projectId],
  )
  const groups = useMemo(() => groupRuns(visibleRuns), [visibleRuns])
  const selectedRun = useMemo(
    () => visibleRuns.find(run => run.id === selectedRunId) ?? null,
    [visibleRuns, selectedRunId],
  )

  const selectRun = useCallback((runId: string) => {
    setSelectedRunId(runId)
    syncUrl({ runId })
  }, [syncUrl])

  /**
   * 成片进哪个草稿箱：绑在项目上，一次选好以后每次出片都落同一个箱子。
   * 不选就是后端的默认箱，所以这里允许空值。
   */
  const changeDraftGroup = useCallback(async (value: string) => {
    if (!projectId)
      return
    const groupId = value === DEFAULT_DRAFT_GROUP ? '' : value
    const prev = projectGroups[projectId] ?? ''
    setProjectGroups(groups => ({ ...groups, [projectId]: groupId }))
    try {
      await updateProject(projectId, { materialGroupId: groupId })
    }
    catch (e) {
      setProjectGroups(groups => ({ ...groups, [projectId]: prev }))
      setError(e instanceof Error ? e.message : t('overview.draftGroupFailed'))
    }
  }, [projectId, projectGroups, t])

  const changeProject = useCallback((value: string) => {
    const next = value === ALL_PROJECTS ? '' : value
    setProjectId(next)
    setSelectedRunId('')
    syncUrl({ projectId: next, runId: '' })
  }, [syncUrl])

  /**
   * 新建任务成功：把新任务选进详情并立刻刷新列表。
   * 详情不再在本地缓存合并——列表接口本来就带 output，右侧详情按 revision 重新拉，
   * 改稿后的新 revision 不会被子任务弹回旧成片。
   */
  const handleCreated = useCallback((runIds: string[]) => {
    const latest = runIds[runIds.length - 1]
    if (latest) {
      setSelectedRunId(latest)
      syncUrl({ runId: latest })
    }
    void refresh()
  }, [refresh, syncUrl])

  const projectOptions = useMemo(() => Object.entries(projects), [projects])
  const { lng } = useParams<{ lng: string }>()
  const onlineMachines = workers.filter(w => w.online)

  /** 队列里还在排队/渲染的任务：只有这两种状态算「进行中」，历史任务堆多了没人看。 */
  const queueActive = useMemo(
    () => renderJobs.filter(job => job.status === 'queued' || job.status === 'running'),
    [renderJobs],
  )
  /** 队列里刚结束的任务：指挥台出的片不进 run 台账，这里给最近两条，否则成片在面板上没有落点。 */
  const queueRecent = useMemo(
    () => renderJobs.filter(job => job.status === 'succeeded' || job.status === 'failed').slice(0, 2),
    [renderJobs],
  )
  const extraCount = useCallback(
    (key: string) => (key === 'run.group.active' ? queueActive.length : key === 'run.group.settled' ? queueRecent.length : 0),
    [queueActive, queueRecent],
  )
  const sections = [
    { key: 'run.group.active', items: groups.active },
    { key: 'run.group.waiting', items: groups.waiting },
    { key: 'run.group.settled', items: groups.settled.slice(0, 8) },
  ] as const

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h1 className="text-lg font-semibold text-foreground">{t('overview.title')}</h1>
          {loading && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
        </div>
        <div className="flex items-center gap-2">
          <Select value={projectId || ALL_PROJECTS} onValueChange={changeProject}>
            <SelectTrigger className="h-8 w-[180px] text-xs" aria-label={t('overview.project')}>
              <SelectValue placeholder={t('overview.allProjects')} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_PROJECTS}>{t('overview.allProjects')}</SelectItem>
              {projectOptions.map(([id, name]) => (
                <SelectItem key={id} value={id}>{name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {projectId && (
            <Button asChild variant="outline" size="sm" className="h-8 text-xs">
              <Link href={`/${lng ?? 'zh-CN'}/studio/projects/${projectId}`}>{t('project.title')}</Link>
            </Button>
          )}
          {projectId && (
            <Select value={projectGroups[projectId] || DEFAULT_DRAFT_GROUP} onValueChange={changeDraftGroup}>
              <SelectTrigger className="h-8 w-[160px] text-xs" aria-label={t('overview.draftGroup')}>
                <SelectValue placeholder={t('overview.draftGroupDefault')} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={DEFAULT_DRAFT_GROUP}>{t('overview.draftGroupDefault')}</SelectItem>
                {draftGroups.map(group => (
                  <SelectItem key={group.id} value={group.id}>{group.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Button variant="outline" size="sm" className="h-8 cursor-pointer" onClick={() => setShowManual(v => !v)}>
            <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', showManual && 'rotate-180')} />
            {t('overview.manual')}
          </Button>
        </div>
      </div>

      {error && (
        <Card className="border-destructive/40">
          <CardContent className="flex items-center justify-between gap-3 p-3">
            <p role="alert" className="flex items-center gap-2 text-sm text-destructive">
              <AlertCircle className="h-4 w-4" />
              {error}
            </p>
            <Button variant="outline" size="sm" className="cursor-pointer" onClick={() => void refresh()}>
              {t('overview.retry')}
            </Button>
          </CardContent>
        </Card>
      )}

      <AssistantPanel
        projectId={projectId}
        projectName={projectId ? projects[projectId] : undefined}
        runs={visibleRuns}
        selectedRunId={selectedRunId}
      />

      <div className="grid items-start gap-3 lg:grid-cols-3">
        <div className="flex flex-col gap-3 lg:col-span-2">
          {sections.map(section => (
            <Card key={section.key}>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-base">
                  {t(section.key)}
                  {section.items.length + extraCount(section.key) > 0 && (
                    <Badge variant="secondary">{section.items.length + extraCount(section.key)}</Badge>
                  )}
                </CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-2">
                {section.items.length === 0 && extraCount(section.key) === 0
                  ? <p className="py-3 text-center text-sm text-muted-foreground">{t('run.empty')}</p>
                  : (
                      <>
                        {section.key === 'run.group.active' && queueActive.map(job => <QueueJobRow key={job.id} job={job} />)}
                        {section.key === 'run.group.settled' && queueRecent.map(job => <QueueJobRow key={job.id} job={job} />)}
                        {section.items.map(run => (
                          <RunRow
                            key={run.id}
                            run={run}
                            projectName={projects[run.projectId]}
                            selected={run.id === selectedRunId}
                            onSelect={selectRun}
                            onChanged={refresh}
                          />
                        ))}
                      </>
                    )}
              </CardContent>
            </Card>
          ))}
        </div>

        <div className="lg:sticky lg:top-4">
          {selectedRun
            ? (
                <RunDetailPanel
                  run={selectedRun}
                  projectName={projects[selectedRun.projectId]}
                  onChanged={refresh}
                />
              )
            : <p className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">{t('run.detail.empty')}</p>}
        </div>
      </div>

      {showManual && (
        <div className="grid gap-3 md:grid-cols-2">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <UserRound className="h-4 w-4" />
                {t('overview.cast')}
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
                {t('overview.voice')}
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
                {t('overview.newTimeline')}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <NewRunForm projects={projects} projectId={projectId} onCreated={handleCreated} />
            </CardContent>
          </Card>

          <FactCardForm projects={projects} projectId={projectId} onCreated={handleCreated} />
        </div>
      )}

      {publications.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">{t('overview.ledger')}</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {publications.slice(0, 8).map(row => <PublicationRow key={row.id} row={row} />)}
          </CardContent>
        </Card>
      )}

      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>{t('overview.executors')}</span>
        {workers.length === 0
          ? <span className="text-warning">{t('overview.workerNone')}</span>
          : workers.map(({ name, online }) => (
              <Badge
                key={name}
                variant="secondary"
                className={online ? 'bg-success/10 text-success' : 'text-muted-foreground'}
              >
                {name}
                {' '}
                {online ? t('overview.workerOnline') : t('overview.workerOffline')}
              </Badge>
            ))}
        {onlineMachines.length === 0 && workers.length > 0 && (
          <span className="text-warning">{t('overview.workerAllOffline')}</span>
        )}
      </div>
    </div>
  )
}
