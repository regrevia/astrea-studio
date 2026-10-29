/**
 * ShotRunCard - 用真实视频素材排一条任务
 *
 * 列出可用的视频片段与时长，选中几条、定个目标时长，就排成一条竖屏任务。
 */

'use client'

import type { MaterialMusic, MaterialShot } from '@/api/production/production.types'
import { Clapperboard, Loader2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { createRun, createRunBatch, fetchMaterialFrameUrl, getMaterialShots, getMaterialMusic, getMusicBeats } from '@/api/production/production.api'
import { useTransClient } from '@/app/i18n/client'
import { buildMaterialTimeline, composeTextPlan, TARGET_SECONDS, type MaterialBeatPlan, type TargetSeconds } from '@/utils/studio/material-timeline'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

interface ShotRunCardProps {
  projectId: string
  projectName: string
  onCreated: (runIds: string[]) => void
}

export function ShotRunCard({ projectId, projectName, onCreated }: ShotRunCardProps) {
  const { t } = useTransClient('studio')
  const [clips, setClips] = useState<MaterialShot[]>([])
  const [loading, setLoading] = useState(true)
  const [reachable, setReachable] = useState(true)
  /** 选中的素材按项目记住，下次进这个项目还在。 */
  const pickedKey = `studio:workspace:clips:${projectId}`
  const [picked, setPicked] = useState<string[]>([])
  const [frames, setFrames] = useState<Record<string, string>>({})
  const [target, setTarget] = useState<TargetSeconds>(30)
  const [tracks, setTracks] = useState<MaterialMusic[]>([])
  /** 空串表示不加配乐。 */
  const [musicRel, setMusicRel] = useState('')
  const [title, setTitle] = useState('')
  const [subtitle, setSubtitle] = useState('')
  const [hookOpen, setHookOpen] = useState('')
  const [hookMid, setHookMid] = useState('')
  const [hookEnd, setHookEnd] = useState('')
  const [beatOn, setBeatOn] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    setLoading(true)
    getMaterialShots()
      .then((res) => {
        if (!alive)
          return
        const rows = (res?.data?.clips ?? []).filter(row => row.kind === 'video' && (row.duration ?? 0) >= 1)
        setClips(rows)
        setReachable(true)
      })
      .catch(() => { if (alive) setReachable(false) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    let alive = true
    getMaterialMusic()
      .then((res) => {
        if (alive && res?.code === 0)
          setTracks(res.data?.tracks ?? [])
      })
      .catch(() => {})
    return () => { alive = false }
  }, [])

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(pickedKey)
      const stored: unknown = raw ? JSON.parse(raw) : []
      setPicked(Array.isArray(stored) ? stored.filter((item): item is string => typeof item === 'string') : [])
    }
    catch {
      setPicked([])
    }
  }, [pickedKey])

  // 缩略图只抽一次：视频取第 1 秒那帧，时长不足 1 秒的取中间。
  useEffect(() => {
    let alive = true
    const created: string[] = []
    clips.slice(0, 12).forEach((clip) => {
      fetchMaterialFrameUrl(clip.rel, Math.min(1, (clip.duration ?? 1) / 2))
        .then((url) => {
          if (!url || !alive)
            return
          created.push(url)
          setFrames(current => ({ ...current, [clip.rel]: url }))
        })
        .catch(() => {})
    })
    return () => {
      alive = false
      created.forEach(url => URL.revokeObjectURL(url))
    }
  }, [clips])

  const pickedClips = useMemo(() => clips.filter(clip => picked.includes(clip.rel)), [clips, picked])

  const togglePick = useCallback((rel: string) => {
    setPicked((current) => {
      const next = current.includes(rel) ? current.filter(item => item !== rel) : [...current, rel]
      window.localStorage.setItem(pickedKey, JSON.stringify(next))
      return next
    })
  }, [pickedKey])

  const pickedTrack = useMemo(() => tracks.find(track => track.rel === musicRel), [tracks, musicRel])

  const buildTextPlan = useCallback(
    () => composeTextPlan({ title, subtitle, hookOpen, hookMid, hookEnd }),
    [hookEnd, hookMid, hookOpen, subtitle, title],
  )

  const submit = useCallback(async (count = 1) => {
    let beatPlan: MaterialBeatPlan | null = null
    if (pickedTrack) {
      beatPlan = { src: pickedTrack.path, bpm: 0, offset: 0, beats: [] }
      if (beatOn) {
        try {
          const res = await getMusicBeats(pickedTrack.rel)
          const data = res?.data
          if (res?.code === 0 && data && data.bpm > 0 && data.beats.length)
            beatPlan = { src: pickedTrack.path, bpm: data.bpm, offset: data.offset, beats: data.beats }
          else
            setError(t('project.clipBeatsFailed'))
        }
        catch {
          setError(t('project.clipBeatsFailed'))
        }
      }
    }
    const spec = buildMaterialTimeline(
      pickedClips.map(clip => ({ src: clip.path, duration: clip.duration ?? 0 })),
      target,
      beatPlan,
      buildTextPlan(),
    )
    if (!spec) {
      setError(t('project.clipFailed'))
      return
    }
    setSubmitting(true)
    setError('')
    try {
      const body = {
        projectId,
        title: `${projectName} · ${target} 秒`,
        brief: pickedTrack
          ? `${target} 秒竖屏：${pickedClips.length} 条素材${beatOn ? '，按鼓点切' : ''}`
          : `${target} 秒竖屏：${pickedClips.length} 条素材`,
        spec,
      }
      // 出 3 版走批量接口：同一份排布入队三次，后两版自动错开运镜与开场。
      const runIds: string[] = []
      if (count > 1) {
        const res = await createRunBatch({ ...body, count })
        if (res?.code !== 0 || !res?.data?.runIds?.length) {
          setError(res?.message || t('project.clipFailed'))
          return
        }
        runIds.push(...res.data.runIds)
      }
      else {
        const res = await createRun(body)
        const runId = res?.data?.runId
        if (res?.code !== 0 || !runId) {
          setError(res?.message || t('project.clipFailed'))
          return
        }
        runIds.push(runId)
      }
      onCreated(runIds)
    }
    catch {
      setError(t('project.clipFailed'))
    }
    finally {
      setSubmitting(false)
    }
  }, [beatOn, buildTextPlan, onCreated, pickedClips, pickedTrack, projectId, projectName, t, target])

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-1.5 text-sm">
          <Clapperboard className="size-3.5" />
          {t('project.clips')}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {loading && (
          <p className="flex items-center gap-1.5 py-2 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            {t('project.clipsLoading')}
          </p>
        )}
        {!loading && !reachable && (
          <p className="py-2 text-xs text-muted-foreground">{t('project.clipsUnreachable')}</p>
        )}
        {!loading && reachable && clips.length === 0 && (
          <p className="py-2 text-xs text-muted-foreground">{t('project.clipsEmpty')}</p>
        )}
        {clips.length > 0 && (
          <>
            <div className="grid grid-cols-3 gap-1.5">
              {clips.slice(0, 12).map((clip) => {
                const active = picked.includes(clip.rel)
                return (
                  <button
                    key={clip.rel}
                    type="button"
                    title={clip.rel}
                    aria-pressed={active}
                    className={`relative overflow-hidden rounded-md border text-left transition-colors ${active ? 'border-primary ring-1 ring-primary' : 'border-border hover:border-muted-foreground'}`}
                    onClick={() => togglePick(clip.rel)}
                  >
                    {frames[clip.rel]
                      ? <img src={frames[clip.rel]} alt={clip.rel} className="aspect-video w-full object-cover" />
                      : <span className="flex aspect-video w-full items-center justify-center text-[11px] text-muted-foreground">{t('project.clipsNoFrame')}</span>}
                    <span className="absolute bottom-0 right-0 rounded-tl bg-background/85 px-1 text-[10px] tabular-nums text-foreground">
                      {t('project.clipDuration', { seconds: clip.duration ?? 0 })}
                    </span>
                  </button>
                )
              })}
            </div>
            <div className="grid gap-1.5">
              <Input
                className="h-8 text-xs"
                value={title}
                maxLength={18}
                aria-label={t('project.clipTitle')}
                placeholder={t('project.clipTitlePlaceholder')}
                onChange={event => setTitle(event.target.value)}
              />
              <Input
                className="h-8 text-xs"
                value={subtitle}
                maxLength={26}
                aria-label={t('project.clipSubtitle')}
                placeholder={t('project.clipSubtitlePlaceholder')}
                onChange={event => setSubtitle(event.target.value)}
              />
              <div className="grid grid-cols-3 gap-1.5">
                <Input
                  className="h-8 text-xs"
                  value={hookOpen}
                  maxLength={24}
                  aria-label={t('project.clipHookOpen')}
                  placeholder={t('project.clipHookOpenPlaceholder')}
                  onChange={event => setHookOpen(event.target.value)}
                />
                <Input
                  className="h-8 text-xs"
                  value={hookMid}
                  maxLength={24}
                  aria-label={t('project.clipHookMid')}
                  placeholder={t('project.clipHookMid')}
                  onChange={event => setHookMid(event.target.value)}
                />
                <Input
                  className="h-8 text-xs"
                  value={hookEnd}
                  maxLength={24}
                  aria-label={t('project.clipHookEnd')}
                  placeholder={t('project.clipHookEnd')}
                  onChange={event => setHookEnd(event.target.value)}
                />
              </div>
            </div>
            <div className="flex items-center justify-between gap-2">
              <Badge variant="secondary">{t('project.clipsPicked', { count: pickedClips.length })}</Badge>
              <div className="flex items-center gap-2">
                <Select value={String(target)} onValueChange={value => setTarget(Number(value) as TargetSeconds)}>
                  <SelectTrigger className="h-8 w-24" aria-label={t('project.clipTarget')}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TARGET_SECONDS.map(seconds => (
                      <SelectItem key={seconds} value={String(seconds)}>
                        {t('project.clipSeconds', { seconds })}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  size="sm"
                  variant="secondary"
                  className="cursor-pointer"
                  disabled={submitting || pickedClips.length === 0}
                  onClick={() => submit(3)}
                >
                  {t('project.clipBatch')}
                </Button>
                <Button
                  size="sm"
                  className="cursor-pointer"
                  disabled={submitting || pickedClips.length === 0}
                  onClick={() => submit(1)}
                >
                  {submitting && <Loader2 className="size-3.5 animate-spin" />}
                  {t('project.clipSubmit')}
                </Button>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Select
                value={musicRel || 'none'}
                onValueChange={(value) => {
                  const next = value === 'none' ? '' : value
                  setMusicRel(next)
                  setBeatOn(true)
                }}
              >
                <SelectTrigger className="h-8 w-36" aria-label={t('project.clipMusic')}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">{t('project.clipMusicNone')}</SelectItem>
                  {tracks.map(track => (
                    <SelectItem key={track.rel} value={track.rel}>{track.rel.replace(/^bgm\//, '')}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {pickedTrack && (
                <div className="flex items-center gap-1.5">
                  <Checkbox
                    id="shot-run-beat"
                    checked={beatOn}
                    onCheckedChange={checked => setBeatOn(checked === true)}
                  />
                  <Label htmlFor="shot-run-beat" className="cursor-pointer text-xs font-normal text-muted-foreground">
                    {t('project.clipBeat')}
                  </Label>
                </div>
              )}
            </div>
          </>
        )}
        {error && <p className="text-xs text-destructive">{error}</p>}
      </CardContent>
    </Card>
  )
}

export default ShotRunCard
