/**
 * ClaimExportDialog - 认领人工导出的成片
 *
 * 剪映导出面板只能人手点，用户在自己机器上导完之后，把文件路径贴回来，
 * 后端按草稿 sidecar 的时长/画布/帧率做体检，过了才入素材库，不过就退回。
 */
'use client'

import type { ProductionRun } from '@/api/production/production.types'
import { useEffect, useState } from 'react'
import { startRunExport } from '@/api/production/production.api'
import { useTransClient } from '@/app/i18n/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'

interface ClaimExportDialogProps {
  run: ProductionRun
  open: boolean
  onOpenChange: (open: boolean) => void
  onClaimed?: () => void
}

export function ClaimExportDialog({ run, open, onOpenChange, onClaimed }: ClaimExportDialogProps) {
  const { t } = useTransClient('studio')
  const [path, setPath] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  // 每次打开都从空路径开始：贴错上一个任务的成片不该被继承成默认值。
  useEffect(() => {
    if (open) {
      setPath('')
      setError('')
    }
  }, [open])

  const draftPath = typeof run.output?.draftPath === 'string' ? run.output.draftPath : ''

  const handleSubmit = async () => {
    const next = path.trim()
    if (!next) {
      setError('run.claim.empty')
      return
    }
    setSubmitting(true)
    setError('')
    try {
      const res = await startRunExport(run.id, { claimPath: next, draftPath })
      if (!res || res.code !== 0) {
        setError(res?.message || 'run.error.claimExport')
        return
      }
      onClaimed?.()
      onOpenChange(false)
    }
    finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('run.claim.title')}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-2">
          <p className="text-xs text-muted-foreground">{t('run.claim.hint')}</p>
          <Input
            value={path}
            onChange={event => setPath(event.target.value)}
            placeholder={t('run.claim.placeholder')}
            aria-label={t('run.claim.title')}
          />
          {error && <p role="alert" className="text-xs text-destructive">{t(error)}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" className="cursor-pointer" onClick={() => onOpenChange(false)}>
            {t('run.claim.cancel')}
          </Button>
          <Button className="cursor-pointer" loading={submitting} onClick={handleSubmit}>
            {t('run.claim.submit')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default ClaimExportDialog
