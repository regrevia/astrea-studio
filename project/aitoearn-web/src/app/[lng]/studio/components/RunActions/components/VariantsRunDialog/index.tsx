/**
 * VariantsRunDialog - 同题多版
 *
 * 一条片子出过以后稿子不用再写一遍：拿它的规格再排几版，只错开运镜方向、
 * 开场镜头和开头字幕，跑完摆在一起挑。第 1 版永远按原稿出。
 */
'use client'

import type { ProductionRun } from '@/api/production/production.types'
import { useState } from 'react'
import type { RunBatchVariant } from '@/api/production/production.api'
import { createRunVariants } from '@/api/production/production.api'
import { useTransClient } from '@/app/i18n/client'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

/** 能错开的三个维度，和后端批次参数一一对应。 */
const DIMS: RunBatchVariant[] = ['mirror', 'opening', 'hook']
const COUNTS = [2, 3, 4]

interface VariantsRunDialogProps {
  run: ProductionRun
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated?: (runIds: string[]) => void
}

export function VariantsRunDialog({ run, open, onOpenChange, onCreated }: VariantsRunDialogProps) {
  const { t } = useTransClient('studio')
  const [count, setCount] = useState('3')
  const [dims, setDims] = useState<RunBatchVariant[]>(DIMS)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(0)

  const toggleDim = (value: RunBatchVariant, checked: boolean) => {
    setDims(current => (checked ? [...current, value] : current.filter(item => item !== value)))
  }

  const handleSubmit = async () => {
    setSubmitting(true)
    setError('')
    try {
      const res = await createRunVariants(run.id, {
        count: Number(count) || 2,
        ...(dims.length ? { variants: dims } : {}),
      })
      const ids = res?.data?.runIds ?? []
      if (!res || res.code !== 0 || ids.length === 0) {
        setError(res?.message || 'run.error.variants')
        return
      }
      setDone(res.data?.count ?? ids.length)
      onCreated?.(ids)
    }
    finally {
      setSubmitting(false)
    }
  }

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      setDone(0)
      setError('')
    }
    onOpenChange(next)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('run.variants.title')}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <p className="text-xs text-muted-foreground">{t('run.variants.hint')}</p>
          <div className="flex items-center gap-2">
            <Label htmlFor="variants-count" className="text-sm font-normal">{t('run.variants.count')}</Label>
            <Select value={count} onValueChange={setCount}>
              <SelectTrigger id="variants-count" className="h-8 w-24 cursor-pointer">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {COUNTS.map(item => (
                  <SelectItem key={item} value={String(item)}>{item}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <fieldset className="flex flex-col gap-2">
            {DIMS.map(dim => (
              <div key={dim} className="flex items-center gap-2">
                <Checkbox
                  id={`variants-dim-${dim}`}
                  checked={dims.includes(dim)}
                  onCheckedChange={next => toggleDim(dim, next === true)}
                />
                <Label htmlFor={`variants-dim-${dim}`} className="cursor-pointer text-sm font-normal">
                  {t(`run.variants.dim.${dim}`)}
                </Label>
              </div>
            ))}
          </fieldset>
          {done > 0 && <p className="text-sm text-foreground">{t('run.variants.done', { count: done })}</p>}
          {error && <p role="alert" className="text-sm text-destructive">{t(error)}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" className="cursor-pointer" onClick={() => handleOpenChange(false)}>
            {t('run.variants.cancel')}
          </Button>
          <Button className="cursor-pointer" loading={submitting} disabled={done > 0} onClick={handleSubmit}>
            {t('run.variants.submit')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default VariantsRunDialog
