/**
 * ReviseRunDialog - 改稿弹窗
 *
 * 改稿只做一件事：把新的要求写进 brief，后端 bump revision 后重新排队。
 * 不在这里改 spec，spec 由表单决定；用户想换稿子结构就重建一条任务。
 */
'use client'

import type { ProductionRun } from '@/api/production/production.types'
import { useState } from 'react'
import { reviseRun } from '@/api/production/production.api'
import { useTransClient } from '@/app/i18n/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'

interface ReviseRunDialogProps {
  run: ProductionRun
  open: boolean
  onOpenChange: (open: boolean) => void
  onRevised?: (revision: number) => void
}

export function ReviseRunDialog({ run, open, onOpenChange, onRevised }: ReviseRunDialogProps) {
  const { t } = useTransClient('studio')
  const [brief, setBrief] = useState(run.brief ?? '')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async () => {
    const next = brief.trim()
    if (!next) {
      setError('run.revise.empty')
      return
    }
    setSubmitting(true)
    setError('')
    try {
      const res = await reviseRun(run.id, { brief: next })
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
