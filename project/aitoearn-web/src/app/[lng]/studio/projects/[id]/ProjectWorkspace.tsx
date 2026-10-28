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
import { useParams } from 'next/navigation'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { getInventory, getProjects, getRuns } from '@/api/production/production.api'
import { useTransClient } from '@/app/i18n/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import NewRunForm from '../../components/NewRunForm'
import { RunDetailPanel } from '../../components/RunDetailPanel'
import { RunRow } from '../../components/RunRow'

/** 任务在跑时靠轮询对齐状态；渲染本身是分钟级，3 秒够用。 */
const POLL_MS = 3000

export default function ProjectWorkspace() {
  const { t } = useTransClient('studio')
  const params = useParams<{ lng: string, id: string }>()
  const lng = params?.lng ?? 'zh-CN'
  const projectId = params?.id ?? ''

  const [project, setProject] = useState<ProductionProject | null>(null)
  const [runs, setRuns] = useState<ProductionRun[]>([])
  const [inventory, setInventory] = useState<Inventory | null>(null)
  const [selectedRunId, setSelectedRunId] = useState('')
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    if (!projectId)
      return
    // http.get 回的是整个信封（{code,data,message}），载荷在 .data 里。
    const [projectsRes, runsRes] = await Promise.all([
      getProjects(),
      getRuns({ projectId, pageSize: 12 }),
    ])
    const projects = projectsRes?.data ?? []
    setProject(projects.find(item => item.id === projectId) ?? null)
    setRuns(runsRes?.data?.list ?? [])
  }, [projectId])

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
    let alive = true
    getInventory().then((data) => { if (alive) setInventory(data) }).catch(() => {})
    return () => { alive = false }
  }, [])

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
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">{t('project.runs')}</CardTitle>
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
                  <div className="mt-1 flex flex-wrap gap-1">
                    {pack.refs.slice(0, 6).map(ref => (
                      <span key={ref} className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                        {ref.split('/').pop() ?? ref}
                      </span>
                    ))}
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
