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
import { createProject, fetchMaterialBlobUrl, getInventory, getProjects, getRuns } from '@/api/production/production.api'
import { useTransClient } from '@/app/i18n/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
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

/** 任务在跑时靠轮询对齐状态；渲染本身是分钟级，3 秒够用。 */
const POLL_MS = 3000
/** 一屏 12 条，点「加载更多」多要 12 条。 */
const PAGE_SIZE = 12

/** 任务区能筛的几档状态；all 表示不加条件。 */
type RunFilter = 'all' | 'succeeded' | 'running' | 'failed'
const RUN_FILTERS: RunFilter[] = ['all', 'succeeded', 'running', 'failed']

/** `pack:slug/name` 对应的素材原图相对路径，和后端只读图片通道约定一致。 */
function packShotPath(ref: string): string | null {
  if (!ref.startsWith('pack:'))
    return null
  const [slug, name] = ref.slice(5).split('/')
  if (!slug || !name)
    return null
  return `packs/${slug}/shots/${name}.png`
}

/** 材质原图要带登录头，图片标签带不上，所以取回 blob URL 再用。 */
function useMaterialThumbs(refs: string[]) {
  const [thumbs, setThumbs] = useState<Record<string, string>>({})

  useEffect(() => {
    let alive = true
    const created: string[] = []
    refs.forEach((ref) => {
      const rel = packShotPath(ref)
      if (!rel)
        return
      fetchMaterialBlobUrl(rel)
        .then((url) => {
          if (!url || !alive)
            return
          created.push(url)
          setThumbs(current => ({ ...current, [ref]: url }))
        })
        .catch(() => {})
    })
    return () => {
      alive = false
      created.forEach(url => URL.revokeObjectURL(url))
    }
  }, [refs])

  return thumbs
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

  const [project, setProject] = useState<ProductionProject | null>(null)
  const [runs, setRuns] = useState<ProductionRun[]>([])
  const [inventory, setInventory] = useState<Inventory | null>(null)
  const [selectedRunId, setSelectedRunId] = useState('')
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

  const projectName = project?.name ?? ''
  const projectMap = useMemo<Record<string, string>>(
    () => (project ? { [project.id]: project.name } : {}),
    [project],
  )
  const selectedRun = useMemo(
    () => runs.find(item => item.id === selectedRunId) ?? null,
    [runs, selectedRunId],
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
        <Button variant="outline" size="sm" className="shrink-0" onClick={() => setCreateOpen(true)}>
          {t('project.create')}
        </Button>
      </div>

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
