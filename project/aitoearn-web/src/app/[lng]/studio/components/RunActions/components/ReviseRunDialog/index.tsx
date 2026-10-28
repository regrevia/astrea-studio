/**
 * ReviseRunDialog - 改稿弹窗
 *
 * 两条路：写一句要求交给后端重新理解；或者在已出片的稿子上直接点某一句改
 * 台词，改哪句就只重渲染哪句。规格结构本身不在这里动。
 */
'use client'

import type { ProductionRun } from '@/api/production/production.types'
import { useEffect, useMemo, useState } from 'react'
import type { SceneRevision } from '@/api/production/production.api'
import { getRun, reviseRun } from '@/api/production/production.api'
import { useTransClient } from '@/app/i18n/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'

/** 从任务规格里取出已展开的字幕行；没有时间轴的稿子返回空数组。 */
function subtitleLines(run: ProductionRun): string[] {
  const timeline = (run.spec as Record<string, unknown> | undefined)?.['timeline'] as Record<string, unknown> | undefined
  const rows = Array.isArray(timeline?.['text']) ? timeline?.['text'] as Array<Record<string, unknown>> : []
  return rows.map(row => (typeof row['text'] === 'string' ? row['text'] : ''))
}

interface ReviseRunDialogProps {
  run: ProductionRun
  open: boolean
  onOpenChange: (open: boolean) => void
  onRevised?: (revision: number) => void
}

export function ReviseRunDialog({ run, open, onOpenChange, onRevised }: ReviseRunDialogProps) {
  const { t } = useTransClient('studio')
  const [brief, setBrief] = useState(run.brief ?? '')
  const [original, setOriginal] = useState<string[]>(() => subtitleLines(run))
  const [drafts, setDrafts] = useState<Record<number, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  // 列表里的 run 可能是上一次轮询的快照，改稿前按 id 取一遍当前版本，
  // 否则会把旧台词当成现状写回去。
  useEffect(() => {
    if (!open) {
      return
    }
    let alive = true
    getRun(run.id)
      .then((res) => {
        const fresh = res?.data?.run
        if (!alive || !fresh) {
          return
        }
        setOriginal(subtitleLines(fresh))
        setDrafts({})
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [open, run.id])

  const scenes: SceneRevision[] = useMemo(
    () => Object.entries(drafts)
      .map(([key, text]) => ({ index: Number(key), text: text.trim() }))
      .filter(row => row.text && row.text !== original[row.index]),
    [drafts, original],
  )

  const handleSubmit = async () => {
    const next = brief.trim()
    if (!next && scenes.length === 0) {
      setError('run.revise.empty')
      return
    }
    setSubmitting(true)
    setError('')
    try {
      const res = await reviseRun(run.id, {
        ...(next ? { brief: next } : {}),
        ...(scenes.length ? { scenes } : {}),
      })
      if (!res || res.code !== 0) {
        setError(res?.message || 'run.error.revise')
        return
      }
      onRevised?.(res.data?.revision ?? (run.revision ?? 0) + 1)
      onOpenChange(false)
    }
    finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('run.revise.title')}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-2">
          <p className="text-xs text-muted-foreground">{t('run.detail.revision', { n: (run.revision ?? 0) + 1 })}</p>
          {original.length > 0 && (
            <div className="flex flex-col gap-2">
              <p className="text-xs font-medium">{t('run.revise.scenes')}</p>
              {original.map((line, index) => (
                <div key={index} className="flex flex-col gap-1">
                  <span className="text-xs text-muted-foreground">
                    {t('run.revise.line', { n: index + 1 })} · {line}
                  </span>
                  <Input
                    value={drafts[index] ?? ''}
                    placeholder={t('run.revise.linePlaceholder')}
                    onChange={(event) => {
                      const value = event.target.value
                      setDrafts(current => ({ ...current, [index]: value }))
                    }}
                  />
                </div>
              ))}
            </div>
          )}
          <Textarea
            rows={4}
            value={brief}
            placeholder={t('run.revise.placeholder')}
            onChange={event => setBrief(event.target.value)}
          />
          {error && <p role="alert" className="text-sm text-destructive">{t(error)}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" className="cursor-pointer" onClick={() => onOpenChange(false)}>
            {t('run.revise.cancel')}
          </Button>
          <Button className="cursor-pointer" loading={submitting} onClick={handleSubmit}>
            {t('run.revise.submit')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default ReviseRunDialog
