/**
 * MaterialSearch - 搜素材并加入
 *
 * 关键词 → 候选 → 加入：加入后把落盘的绝对路径追加进素材行，
 * 「第几条进第几屏」的规则不变。
 */
'use client'

import type { MaterialCandidate } from '@/api/production/production.api'
import { useMemo, useState } from 'react'
import { landMaterials, searchMaterials } from '@/api/production/production.api'
import { useTransClient } from '@/app/i18n/client'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

interface MaterialSearchProps {
  /** 已选中的素材行，用来标记「已加入」。 */
  picked: string[]
  /** 落盘成功后把绝对路径交回去。 */
  onPick: (path: string) => void
}

const hintClass = 'text-[11px] text-muted-foreground'

function sizeText(bytes: number) {
  if (!bytes)
    return ''
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`
}

export default function MaterialSearch({ picked, onPick }: MaterialSearchProps) {
  const { t } = useTransClient('studio')
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [kind, setKind] = useState<'video' | 'image'>('video')
  const [rows, setRows] = useState<MaterialCandidate[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [landing, setLanding] = useState('')
  const [error, setError] = useState('')
  const pickedSet = useMemo(() => new Set(picked), [picked])

  const search = async () => {
    const text = query.trim()
    if (!text || searching)
      return
    setSearching(true)
    setError('')
    setRows(await searchMaterials(text, kind))
    setSearching(false)
  }

  const add = async (item: MaterialCandidate) => {
    if (landing)
      return
    setLanding(item.id)
    setError('')
    try {
      const landed = await landMaterials([item])
      const path = landed[0]?.path
      if (!path) {
        setError(t('run.material.failed'))
        return
      }
      onPick(path)
    }
    catch {
      setError(t('run.material.failed'))
    }
    finally {
      setLanding('')
    }
  }

  if (!open) {
    return (
      <Button type="button" size="sm" variant="ghost" className="self-start" onClick={() => setOpen(true)}>
        {t('run.material.open')}
      </Button>
    )
  }

  return (
    <div className="flex flex-col gap-2 rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={query}
          placeholder={t('run.material.placeholder')}
          className="h-8 max-w-56"
          onChange={event => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              void search()
            }
          }}
        />
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={!query.trim() || searching}
          onClick={() => void search()}
        >
          {searching ? t('run.material.searching') : t('run.material.search')}
        </Button>
        <Button
          type="button"
          size="sm"
          variant={kind === 'image' ? 'default' : 'outline'}
          onClick={() => setKind(current => (current === 'video' ? 'image' : 'video'))}
        >
          {kind === 'video' ? t('run.material.video') : t('run.material.image')}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
          {t('run.material.collapse')}
        </Button>
      </div>
      {error && <span className="text-[11px] text-destructive">{error}</span>}
      {rows !== null && rows.length === 0 && !searching && (
        <span className={hintClass}>{t('run.material.empty')}</span>
      )}
      {rows !== null && rows.map(item => (
        <div key={`${item.provider}-${item.id}`} className="flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-xs" title={item.title}>
            {item.title}
          </span>
          <span className={`${hintClass} shrink-0`}>{item.license || t('run.material.licenseUnknown')}</span>
          <span className={`${hintClass} shrink-0`}>{sizeText(item.bytes)}</span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="shrink-0"
            disabled={landing === item.id}
            onClick={() => void add(item)}
          >
            {landing === item.id ? t('run.material.adding') : t('run.material.add')}
          </Button>
        </div>
      ))}
      {rows !== null && rows.some(item => item.page) && (
        <span className={hintClass}>{t('run.material.source')}</span>
      )}
      {pickedSet.size > 0 && (
        <span className={hintClass}>{t('run.material.picked', { n: pickedSet.size })}</span>
      )}
    </div>
  )
}
