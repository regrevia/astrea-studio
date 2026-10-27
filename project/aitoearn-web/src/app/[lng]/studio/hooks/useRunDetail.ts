/**
 * studio/hooks/useRunDetail - 按 runId 拉任务详情，只收更新的版本
 *
 * 页面的列表每 3 秒刷新，列表里的 status/revision/output 是权威的；详情接口多给
 * jobs/attempts/events。两路数据混用时最怕旧详情盖新状态（例如 R1 的 succeeded 详情
 * 覆盖掉 R2 的 running），所以这里统一把关：revision 更高才收，同 revision 比 updatedAt，
 * 再退到事件 seq。列表 revision 变了会重新拉一次，已落定的任务也跟得上改稿重跑。
 */
'use client'

import type { RunDetail } from '@/api/production/production.types'
import { useCallback, useEffect, useRef, useState } from 'react'
import { getRun } from '@/api/production/production.api'
import { isRunBusy } from '../utils/status'

const POLL_MS = 3000

function maxEventSeq(detail: RunDetail | null): number {
  let seq = 0
  for (const event of detail?.events ?? []) {
    if (typeof event.seq === 'number' && event.seq > seq)
      seq = event.seq
  }
  return seq
}

function timestamp(value?: string): number {
  const parsed = value ? Date.parse(value) : Number.NaN
  return Number.isNaN(parsed) ? 0 : parsed
}

/** 新详情是否比手上的更新。跨 revision 一律听新的，同 revision 再比时间戳和事件序。 */
export function isFresherDetail(next: RunDetail, current: RunDetail | null): boolean {
  if (!current)
    return true
  const nextRevision = next.run.revision ?? 0
  const currentRevision = current.run.revision ?? 0
  if (nextRevision !== currentRevision)
    return nextRevision > currentRevision
  const nextAt = timestamp(next.run.updatedAt)
  const currentAt = timestamp(current.run.updatedAt)
  if (nextAt !== currentAt)
    return nextAt > currentAt
  return maxEventSeq(next) >= maxEventSeq(current)
}

export function useRunDetail(runId: string | null | undefined, watchKey?: string) {
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [error, setError] = useState('')
  const latest = useRef<RunDetail | null>(null)

  const apply = useCallback((next: RunDetail) => {
    if (!isFresherDetail(next, latest.current))
      return false
    latest.current = next
    setDetail(next)
    return true
  }, [])

  // 换任务时清掉上一版，避免详情面板先闪一下别人的成片。
  useEffect(() => {
    latest.current = null
    setDetail(null)
    setError('')
  }, [runId])

  useEffect(() => {
    if (!runId)
      return
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const tick = async () => {
      const res = await getRun(runId)
      if (!alive)
        return
      if (!res || res.code !== 0 || !res.data) {
        setError('run.error.detail')
        timer = setTimeout(tick, POLL_MS)
        return
      }
      setError('')
      apply(res.data)
      // 还在跑就接着轮询；落定后交给列表刷新触发重新拉取。
      if (isRunBusy(latest.current?.run.status))
        timer = setTimeout(tick, POLL_MS)
    }
    tick()
    return () => {
      alive = false
      if (timer)
        clearTimeout(timer)
    }
  }, [runId, watchKey, apply])

  return { detail, error, apply }
}
