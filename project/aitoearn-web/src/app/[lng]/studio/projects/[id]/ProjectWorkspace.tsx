/**
 * ProjectWorkspace - 单个项目的任务与素材现场
 *
 * 只做三件事：这个项目名下的任务、渲染机能用的素材包、往这个项目里排新任务。
 * 任务状态沿用制作台的行与详情，不复制第二套展示逻辑。
 */

'use client'

import type { Inventory, ProductionProject, ProductionRun } from '@/api/production/production.types'
import { ArrowLeft, Package } from 'lucide-react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { createProject, deleteProject, fetchMaterialBlobUrl, getInventory, getProjects, getRuns } from '@/api/production/production.api'
import { useTransClient } from '@/app/i18n/client'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { packShotPath, useMaterialThumbs } from '@/app/[lng]/studio/utils/materials'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import NewRunForm from '../../components/NewRunForm'
import { RunDetailPanel } from '../../components/RunDetailPanel'
import { RunRow } from '../../components/RunRow'
import { BatchCompare } from './components/BatchCompare'
import { ShotRunCard } from './components/ShotRunCard'

/** 任务在跑时靠轮询对齐状态；渲染本身是分钟级，3 秒够用。 */
const POLL_MS = 3000
/** 一屏 12 条，点「加载更多」多要 12 条。 */
const PAGE_SIZE = 12

/** 任务区能筛的几档状态；all 表示不加条件。 */
type RunFilter = 'all' | 'succeeded' | 'running' | 'failed'
const RUN_FILTERS: RunFilter[] = ['all', 'succeeded', 'running', 'failed']

/** 批次时间到秒：同一分钟连出两批时，靠秒数才分得清哪张卡更新。 */
function formatBatchTime(at: number, lng: string): string {
  return new Date(at).toLocaleTimeString(lng === 'en' ? 'en-US' : 'zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

export default function ProjectWorkspace() {
  const { t } = useTransClient('studio')
  const params = useParams<{ lng: string, id: string }>()
  const lng = params?.lng ?? 'zh-CN'
  const projectId = params?.id ?? ''
  const router = useRouter()
  const [createOpen, setCreateOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState('')
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState('')

  const [project, setProject] = useState<ProductionProject | null>(null)
  const [runs, setRuns] = useState<ProductionRun[]>([])
  const [inventory, setInventory] = useState<Inventory | null>(null)
  const [selectedRunId, setSelectedRunId] = useState('')
  // 一次出多版时记住这批 id，面板顶上直接切版本，不用回任务列表里翻。
  // 项目上存了一份，刷新页面后按钮还在。
  const [freshBatchIds, setBatchIds] = useState<string[]>([])
  /** 素材选中结果按项目记住：下次进这个项目还在，不用重新点。 */
  const materialsKey = `studio:workspace:materials:${projectId}`
  const [materialsText, setMaterialsText] = useState('')
  /** 任务区的状态筛选与分页：默认全部、一屏 12 条。 */
  const [status, setStatus] = useState<RunFilter>('all')
  const [pageSize, setPageSize] = useState(PAGE_SIZE)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    if (!projectId)
      return
    // http.get 回的是整个信封（{code,data,message}），载荷在 .data 里。
    const [projectsRes, runsRes] = await Promise.all([
      getProjects(),
      getRuns({ projectId, pageSize, status: status === 'all' ? undefined : status }),
    ])
    const projects = projectsRes?.data ?? []
    setProject(projects.find(item => item.id === projectId) ?? null)
    setRuns(runsRes?.data?.list ?? [])
    setTotal(runsRes?.data?.total ?? 0)
  }, [pageSize, projectId, status])

  useEffect(() => {
    let alive = true
    setLoading(true)
    refresh().catch(() => {}).finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [refresh])

  useEffect(() => {
    let alive = true
    const tick = () => { refresh().catch(() => {}) }
    const timer = window.setInterval(tick, POLL_MS)
    return () => { alive = false; window.clearInterval(timer) }
  }, [refresh])

  useEffect(() => {
    if (!projectId)
      return
    setMaterialsText(window.localStorage.getItem(materialsKey) ?? '')
  }, [materialsKey, projectId])

  const updateMaterials = useCallback((next: string) => {
    setMaterialsText(next)
    window.localStorage.setItem(materialsKey, next)
  }, [materialsKey])

  /** 点素材包里的图：已选中就撤掉，否则加进表单。 */
  const toggleShot = useCallback((ref: string) => {
    const lines = materialsText.split('\n').map(line => line.trim()).filter(Boolean)
    updateMaterials(
      lines.includes(ref) ? lines.filter(line => line !== ref).join('\n') : [...lines, ref].join('\n'),
    )
  }, [materialsText, updateMaterials])

  useEffect(() => {
    let alive = true
    getInventory().then((data) => { if (alive) setInventory(data) }).catch(() => {})
    return () => { alive = false }
  }, [])

  /** 建完直接进新项目的工作区，省掉再找一遍。 */
  const submitCreate = useCallback(async () => {
    const name = newName.trim()
    if (!name || creating)
      return
    setCreating(true)
    setCreateError('')
    try {
      const created = await createProject({ name })
      const id = created?.data?.id
      setCreateOpen(false)
      setNewName('')
      if (id)
        router.push(`/${lng}/studio/projects/${id}`)
    }
    catch {
      setCreateError(t('project.createFailed'))
    }
    finally {
      setCreating(false)
    }
  }, [creating, lng, newName, router, t])

  /** 删完回制作台；还有任务的项目后端会拒绝，错误留在弹窗里。 */
  const submitDelete = useCallback(async () => {
    if (!project || deleting)
      return
    setDeleting(true)
    setDeleteError('')
    try {
      const res = await deleteProject(project.id)
      if (res?.code !== 0) {
        setDeleteError(res?.message || t('project.deleteFailed'))
        return
      }
      setDeleteOpen(false)
      router.push(`/${lng}/studio`)
    }
    catch {
      setDeleteError(t('project.deleteFailed'))
    }
    finally {
      setDeleting(false)
    }
  }, [deleting, lng, project, router, t])

  const projectName = project?.name ?? ''
  const projectMap = useMemo<Record<string, string>>(
    () => (project ? { [project.id]: project.name } : {}),
    [project],
  )
  const selectedRun = useMemo(
    () => runs.find(item => item.id === selectedRunId) ?? null,
    [runs, selectedRunId],
  )
  // 刚出的这批优先，刷新后回落到项目上记的最近两批（新的在前）。
  const batchGroups = useMemo(() => {
    const spent = freshBatchIds.filter(id => id !== '')
    const latest = spent.length > 1 ? [{ runIds: spent, at: Date.now() }] : []
    const older = (project?.recentBatches ?? []).filter(
      batch => latest.length === 0 || batch.runIds.join() !== spent.join(),
    )
    return [...latest, ...older].slice(0, 2)
  }, [freshBatchIds, project?.recentBatches])
  // 只并排还挂在列表里的那几条；翻页翻走的版本不回填，避免半截缩略图。
  const batchCards = useMemo(
    () => batchGroups
      .map(batch => batch.runIds
        .map(id => runs.find(item => item.id === id))
        .filter((run): run is ProductionRun => Boolean(run)))
      .filter(batch => batch.length > 1),
    [batchGroups, runs],
  )
  const packs = inventory?.materials?.packs ?? []
  const packRefs = useMemo(() => packs.flatMap(pack => pack.refs), [packs])
  const thumbs = useMaterialThumbs(packRefs)

  if (!loading && !project) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-10 text-sm text-muted-foreground">
        {t('project.notFound')}
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-4 py-6">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Button asChild variant="ghost" size="sm" className="-ml-2 h-7 px-2 text-xs">
              <Link href={`/${lng}/studio`}>
                <ArrowLeft className="mr-1 size-3.5" />
                {t('project.back')}
              </Link>
            </Button>
            <h1 className="truncate text-lg font-semibold">{projectName || t('project.title')}</h1>
          </div>
          {project?.sourceRepo && (
            <p className="mt-0.5 truncate text-xs text-muted-foreground">
              {t('project.sourceRepo')}：{project.sourceRepo}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => setCreateOpen(true)}>
            {t('project.create')}
          </Button>
          {project && !project.isDefault && (
            <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={() => setDeleteOpen(true)}>
              {t('project.delete')}
            </Button>
          )}
        </div>
      </div>

      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('project.deleteConfirmTitle')}</AlertDialogTitle>
            <AlertDialogDescription>{t('project.deleteConfirmHint')}</AlertDialogDescription>
          </AlertDialogHeader>
          {deleteError && <p role="alert" className="text-xs text-destructive">{deleteError}</p>}
          <AlertDialogFooter>
            <AlertDialogCancel>{t('project.cancel')}</AlertDialogCancel>
            <AlertDialogAction
              disabled={deleting}
              onClick={(event) => { event.preventDefault(); submitDelete().catch(() => {}) }}
            >
              {t('project.deleteSubmit')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t('project.create')}</DialogTitle>
          </DialogHeader>
          <form
            className="space-y-3"
            onSubmit={(event) => { event.preventDefault(); submitCreate().catch(() => {}) }}
          >
            <Input
              value={newName}
              maxLength={40}
              autoFocus
              placeholder={t('project.createPlaceholder')}
              onChange={event => setNewName(event.target.value)}
            />
            {createError && <p role="alert" className="text-xs text-destructive">{createError}</p>}
            <DialogFooter>
              <Button type="button" variant="ghost" size="sm" onClick={() => setCreateOpen(false)}>
                {t('project.cancel')}
              </Button>
              <Button type="submit" size="sm" disabled={creating || !newName.trim()}>
                {t('project.createSubmit')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="text-sm">{t('project.runs')}</CardTitle>
                <Select value={status} onValueChange={value => setStatus(value as RunFilter)}>
                  <SelectTrigger className="h-8 w-28 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {RUN_FILTERS.map(item => (
                      <SelectItem key={item} value={item}>{t(`project.status_${item}`)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </CardHeader>
            <CardContent className="space-y-2">
              {runs.length === 0 && (
                <p className="py-4 text-xs text-muted-foreground">{t('project.emptyRuns')}</p>
              )}
              {runs.map(run => (
                <RunRow
                  key={run.id}
                  run={run}
                  selected={run.id === selectedRunId}
                  onSelect={setSelectedRunId}
                  onChanged={() => { refresh().catch(() => {}) }}
                />
              ))}
              {runs.length < total && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="w-full"
                  onClick={() => setPageSize(current => current + PAGE_SIZE)}
                >
                  {t('project.loadMore', { count: total - runs.length })}
                </Button>
              )}
            </CardContent>
          </Card>

          {batchCards.map((batchRuns, index) => (
            <Card key={batchRuns[0].id}>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">
                  {index === 0 ? t('project.batch') : t('project.batchPrev')}
                  {batchGroups[index]?.at ? ` · ${formatBatchTime(batchGroups[index].at as number, lng)}` : ''}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <BatchCompare
                  runs={batchRuns}
                  selectedId={selectedRunId}
                  onSelect={setSelectedRunId}
                />
              </CardContent>
            </Card>
          ))}

          {selectedRun && (
            <Card>
              <CardContent className="pt-4">
                <RunDetailPanel
                  run={selectedRun}
                  onChanged={() => { refresh().catch(() => {}) }}
                />
              </CardContent>
            </Card>
          )}
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-1.5 text-sm">
                <Package className="size-3.5" />
                {t('project.materials')}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {packs.length === 0 && (
                <p className="py-2 text-xs text-muted-foreground">{t('project.materialsEmpty')}</p>
              )}
              {packs.map(pack => (
                <div key={pack.slug} className="rounded-lg border border-border p-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-xs font-medium">{pack.slug}</span>
                    <Badge variant="secondary">{t('project.materialsShots', { count: pack.shots })}</Badge>
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {pack.refs.map((ref, index) => {
                      const picked = materialsText.split('\n').map(line => line.trim()).includes(ref)
                      return (
                        <button
                          key={ref}
                          type="button"
                          title={ref}
                          aria-pressed={picked}
                          className={`overflow-hidden rounded-md border transition-colors ${picked ? 'border-primary ring-1 ring-primary' : 'border-border hover:border-muted-foreground'}`}
                          onClick={() => toggleShot(ref)}
                        >
                          {thumbs[ref]
                            ? <img src={thumbs[ref]} alt={t('project.materialsShot', { index: index + 1 })} className="size-16 object-cover" />
                            : <span className="flex size-16 items-center justify-center text-[11px] text-muted-foreground">{t('project.materialsShot', { index: index + 1 })}</span>}
                        </button>
                      )
                    })}
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          <ShotRunCard
            projectId={projectId}
            projectName={project?.name ?? ''}
            onCreated={(ids) => {
              setBatchIds(ids.length > 1 ? ids : [])
              if (ids[0])
                setSelectedRunId(ids[0])
              refresh().catch(() => {})
            }}
          />

        </div>
      </div>

      {project && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">{t('project.newRun')}</CardTitle>
          </CardHeader>
          <CardContent>
            <NewRunForm
              projects={projectMap}
              projectId={project.id}
              materialsText={materialsText}
              onMaterialsTextChange={updateMaterials}
              onCreated={(ids) => {
                if (ids[0])
                  setSelectedRunId(ids[0])
                refresh().catch(() => {})
              }}
            />
          </CardContent>
        </Card>
      )}
    </div>
  )
}
