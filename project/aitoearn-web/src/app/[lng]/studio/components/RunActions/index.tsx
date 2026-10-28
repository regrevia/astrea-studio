/**
 * RunActions - 任务的取消 / 重试 / 改稿 / 收剪映导出
 *
 * 列表行和详情面板共用这一份动作，避免两处各写一遍取消语义。
 * 取消和重试会改变后端队列状态，先弹确认；改稿走弹窗写 brief。
 */
'use client'

import type { ProductionRun } from '@/api/production/production.types'
import { Clapperboard, CopyPlus, ExternalLink, FolderInput, PencilLine, RotateCcw, XCircle } from 'lucide-react'
import { useState } from 'react'
import { cancelRun, openRunDraft, retryRun, startRunExport } from '@/api/production/production.api'
import { useTransClient } from '@/app/i18n/client'
import { Button } from '@/components/ui/button'
import { confirm } from '@/utils/ui/confirm'
import { canCancelRun, canRetryRun } from '../../utils/status'
import { ClaimExportDialog } from './components/ClaimExportDialog'
import { ReviseRunDialog } from './components/ReviseRunDialog'
import { VariantsRunDialog } from './components/VariantsRunDialog'

interface RunActionsProps {
  run: ProductionRun
  /** 动作成功后通知页面立刻刷新，不用等下一轮轮询。 */
  onChanged?: () => void
  /** 详情面板里给常规尺寸，列表行里收紧。 */
  compact?: boolean
}

export function RunActions({ run, onChanged, compact = false }: RunActionsProps) {
  const { t } = useTransClient('studio')
  const [pending, setPending] = useState<'cancel' | 'retry' | 'export' | 'open' | null>(null)
  const [error, setError] = useState('')
  const [reviseOpen, setReviseOpen] = useState(false)
  const [claimOpen, setClaimOpen] = useState(false)
  const [variantsOpen, setVariantsOpen] = useState(false)
  // 认领只对「草稿已落盘、用户自己去剪映导过」的任务成立；草稿路径是体检基准。
  const draftPath = typeof run.output?.draftPath === 'string' ? run.output.draftPath : ''

  const size = compact ? 'sm' : 'default'

  const handleCancel = async () => {
    const ok = await confirm({
      title: t('run.action.cancel'),
      content: t('run.confirm.cancel'),
      okType: 'destructive',
      onOk: async () => {
        setPending('cancel')
        setError('')
        try {
          const res = await cancelRun(run.id)
          if (!res || res.code !== 0) {
            setError(res?.message || 'run.error.cancel')
            return
          }
          onChanged?.()
        }
        finally {
          setPending(null)
        }
      },
    })
    return ok
  }

  /** 把草稿在剪映里打开，让人接着改；不开导出面板。 */
  const handleOpenDraft = async () => {
    setPending('open')
    setError('')
    try {
      const res = await openRunDraft(run.id)
      if (!res || res.code !== 0) {
        setError(res?.message || 'run.error.openDraft')
        return
      }
      onChanged?.()
    }
    finally {
      setPending(null)
    }
  }

  /** 导出要在这台 Mac 上点剪映，交给后台任务去做。 */
  const handleExport = async () => {
    await confirm({
      title: t('run.action.exportGui'),
      content: t('run.confirm.exportGui'),
      onOk: async () => {
        setPending('export')
        setError('')
        try {
          const res = await startRunExport(run.id, { allowGui: true })
          if (!res || res.code !== 0) {
            setError(res?.message || 'run.error.exportGui')
            return
          }
          onChanged?.()
        }
        finally {
          setPending(null)
        }
      },
    })
  }

  const handleRetry = async () => {
    await confirm({
      title: t('run.action.retry'),
      content: t('run.confirm.retry'),
      onOk: async () => {
        setPending('retry')
        setError('')
        try {
          const res = await retryRun(run.id)
          if (!res || res.code !== 0) {
            setError(res?.message || 'run.error.retry')
            return
          }
          onChanged?.()
        }
        finally {
          setPending(null)
        }
      },
    })
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {canCancelRun(run.status) && (
        <Button
          size={size}
          variant="outline"
          className="cursor-pointer"
          loading={pending === 'cancel'}
          onClick={handleCancel}
        >
          {!pending && <XCircle className="h-4 w-4" />}
          {t('run.action.cancel')}
        </Button>
      )}
      {canRetryRun(run.status) && (
        <Button
          size={size}
          variant="outline"
          className="cursor-pointer"
          loading={pending === 'retry'}
          onClick={handleRetry}
        >
          {!pending && <RotateCcw className="h-4 w-4" />}
          {t('run.action.retry')}
        </Button>
      )}
      {run.status === 'waiting_user' && (
        <Button
          size={size}
          variant="outline"
          className="cursor-pointer"
          loading={pending === 'export'}
          onClick={handleExport}
        >
          {!pending && <Clapperboard className="h-4 w-4" />}
          {t('run.action.exportGui')}
        </Button>
      )}
      {draftPath && (
        <Button
          size={size}
          variant="outline"
          className="cursor-pointer"
          loading={pending === 'open'}
          onClick={handleOpenDraft}
        >
          {!pending && <ExternalLink className="h-4 w-4" />}
          {t('run.action.openDraft')}
        </Button>
      )}
      {draftPath && run.output?.claimed !== true
        && (run.status === 'waiting_user' || run.status === 'succeeded') && (
        <Button
          size={size}
          variant="outline"
          className="cursor-pointer"
          onClick={() => setClaimOpen(true)}
        >
          <FolderInput className="h-4 w-4" />
          {t('run.action.claimExport')}
        </Button>
      )}
      <Button size={size} variant="outline" className="cursor-pointer" onClick={() => setReviseOpen(true)}>
        <PencilLine className="h-4 w-4" />
        {t('run.action.revise')}
      </Button>
      {run.status === 'succeeded' && (
        <Button size={size} variant="outline" className="cursor-pointer" onClick={() => setVariantsOpen(true)}>
          <CopyPlus className="h-4 w-4" />
          {t('run.action.variants')}
        </Button>
      )}

      {error && <p role="alert" className="text-xs text-destructive">{t(error)}</p>}

      <ClaimExportDialog
        run={run}
        open={claimOpen}
        onOpenChange={setClaimOpen}
        onClaimed={() => onChanged?.()}
      />

      <VariantsRunDialog
        run={run}
        open={variantsOpen}
        onOpenChange={setVariantsOpen}
        onCreated={() => onChanged?.()}
      />

      <ReviseRunDialog
        key={`${run.id}-${run.revision ?? 0}`}
        run={run}
        open={reviseOpen}
        onOpenChange={setReviseOpen}
        onRevised={() => onChanged?.()}
      />
    </div>
  )
}

export default RunActions
