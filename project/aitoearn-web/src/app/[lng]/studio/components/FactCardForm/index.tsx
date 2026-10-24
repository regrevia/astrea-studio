'use client'

/**
 * FactCardForm - 干货卡一键出片表单
 *
 * 填选题、开场钩子、「标题::说明」要点与音色，提交即排一条出片任务。
 * 出片流程逐屏出卡并配音，成品落进剪映草稿箱，不需要手动导入。
 */

import type { FactCardVoice } from '@/api/production/production.types'
import { createFactCardRun } from '@/api/production/production.api'
import { useTransClient } from '@/app/i18n/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { Clapperboard, Plus, Trash2 } from 'lucide-react'
import Link from 'next/link'
import { useState } from 'react'
import {
  DEFAULT_FACT_CARD_KICKER,
  DEFAULT_FACT_CARD_POINTS,
  DEFAULT_FACT_CARD_VOICE,
  FACT_CARD_VOICE_OPTIONS,
  MAX_FACT_CARD_POINTS,
  MIN_FACT_CARD_POINTS,
  buildFactCardRunInput,
  factCardNarrationLineCounts,
  isFactCardVoice,
  validateFactCardForm,
} from './utils'
import type { FactCardFormError, FactCardFormValues } from './utils'

const hintClass = 'text-[11px] text-muted-foreground'

interface FactCardFormProps {
  /** 制作项目 id → 名称，和新建时间轴任务共用同一份项目列表。 */
  projects: Record<string, string>
  /** 任务创建成功，交给页面接轮询。 */
  onCreated: (runId: string) => void
}

function FactCardForm({ projects, onCreated }: FactCardFormProps) {
  const { t } = useTransClient('studio')
  const [topic, setTopic] = useState('')
  const [hook, setHook] = useState('')
  const [narration, setNarration] = useState('')
  const [points, setPoints] = useState<string[]>(
    () => Array.from({ length: DEFAULT_FACT_CARD_POINTS }, () => ''),
  )
  const [voice, setVoice] = useState<FactCardVoice>(DEFAULT_FACT_CARD_VOICE)
  const [kicker, setKicker] = useState(DEFAULT_FACT_CARD_KICKER)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<FactCardFormError | null>(null)
  const [createdRunId, setCreatedRunId] = useState('')

  const projectIds = Object.keys(projects)
  // 和「新建时间轴任务」一致：没手动选项目时用列表第一个。
  const effectiveProjectId = projectIds[0] ?? ''

  function updatePoint(index: number, value: string) {
    setPoints(prev => prev.map((point, i) => (i === index ? value : point)))
  }

  function addPoint() {
    setPoints(prev => (prev.length >= MAX_FACT_CARD_POINTS ? prev : [...prev, '']))
  }

  function removePoint(index: number) {
    setPoints(prev => (prev.length <= MIN_FACT_CARD_POINTS ? prev : prev.filter((_, i) => i !== index)))
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setCreatedRunId('')

    const values: FactCardFormValues = { topic, hook, points, narration, voice, kicker }
    const invalid = validateFactCardForm(values)
    if (invalid) {
      setError(invalid)
      return
    }
    if (!effectiveProjectId) {
      setError({ key: 'factCard.error.noProject' })
      return
    }

    setSubmitting(true)
    try {
      const res = await createFactCardRun(buildFactCardRunInput(values, effectiveProjectId))
      if (res?.code !== 0 || !res?.data?.runId) {
        setError({
          key: 'factCard.error.submitFailed',
          params: { message: res?.message || t('factCard.error.unknown') },
        })
        return
      }
      setCreatedRunId(res.data.runId)
      onCreated(res.data.runId)
    }
    catch (err) {
      setError({
        key: 'factCard.error.submitFailed',
        params: { message: err instanceof Error ? err.message : t('factCard.error.unknown') },
      })
    }
    finally {
      setSubmitting(false)
    }
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <Clapperboard className="h-4 w-4" />
          {t('factCard.title')}
        </CardTitle>
        <CardDescription>{t('factCard.subtitle')}</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="grid gap-4 sm:grid-cols-2" onSubmit={handleSubmit}>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="fact-card-topic">{t('factCard.topic')}</Label>
            <Input
              id="fact-card-topic"
              value={topic}
              placeholder={t('factCard.topicPlaceholder')}
              onChange={event => setTopic(event.target.value)}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="fact-card-kicker">{t('factCard.kicker')}</Label>
            <Input
              id="fact-card-kicker"
              value={kicker}
              onChange={event => setKicker(event.target.value)}
            />
            <span className={hintClass}>{t('factCard.kickerHint')}</span>
          </div>

          <div className="flex flex-col gap-1.5 sm:col-span-2">
            <Label htmlFor="fact-card-hook">{t('factCard.hook')}</Label>
            <Input
              id="fact-card-hook"
              value={hook}
              placeholder={t('factCard.hookPlaceholder')}
              onChange={event => setHook(event.target.value)}
            />
          </div>

          <div className="flex flex-col gap-1.5 sm:col-span-2">
            <Label htmlFor="fact-card-narration">{t('factCard.narration')}</Label>
            <Textarea
              id="fact-card-narration"
              rows={points.length + 2}
              value={narration}
              placeholder={t('factCard.narrationPlaceholder')}
              onChange={event => setNarration(event.target.value)}
            />
            <span className={hintClass}>
              {t('factCard.narrationHint', {
                pointsOnly: factCardNarrationLineCounts(points.length).pointsOnly,
                withIntroOutro: factCardNarrationLineCounts(points.length).withIntroOutro,
              })}
            </span>
          </div>

          <div className="flex flex-col gap-2 sm:col-span-2">
            <div className="flex items-center justify-between">
              <Label>{t('factCard.points')}</Label>
              <span className={hintClass}>
                {t('factCard.pointsCount', { min: MIN_FACT_CARD_POINTS, max: MAX_FACT_CARD_POINTS })}
              </span>
            </div>
            <div className="flex flex-col gap-2">
              {points.map((point, index) => (
                <div key={index} className="flex items-start gap-2">
                  <Textarea
                    id={`fact-card-point-${index}`}
                    rows={2}
                    value={point}
                    placeholder={t('factCard.pointPlaceholder')}
                    aria-label={t('factCard.pointLabel', { index: index + 1 })}
                    onChange={event => updatePoint(index, event.target.value)}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="shrink-0 cursor-pointer"
                    disabled={points.length <= MIN_FACT_CARD_POINTS}
                    aria-label={t('factCard.removePoint')}
                    onClick={() => removePoint(index)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
            <div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="cursor-pointer"
                disabled={points.length >= MAX_FACT_CARD_POINTS}
                onClick={addPoint}
              >
                <Plus className="mr-1.5 h-3.5 w-3.5" />
                {t('factCard.addPoint')}
              </Button>
            </div>
            <span className={hintClass}>{t('factCard.pointsHint')}</span>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="fact-card-voice">{t('factCard.voice')}</Label>
            <Select value={voice} onValueChange={(value) => { if (isFactCardVoice(value)) setVoice(value) }}>
              <SelectTrigger id="fact-card-voice">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {FACT_CARD_VOICE_OPTIONS.map(option => (
                  <SelectItem key={option.value} value={option.value}>{t(option.labelKey)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className={hintClass}>{t('factCard.voiceHint')}</span>
          </div>

          {error && (
            <p className="text-sm text-destructive sm:col-span-2" role="alert">
              {t(error.key, error.params)}
            </p>
          )}

          {createdRunId && (
            <div className="rounded-md border border-border bg-muted/40 p-3 text-sm sm:col-span-2">
              <p className="font-medium text-foreground">{t('factCard.success', { runId: createdRunId })}</p>
              <p className="mt-1 text-muted-foreground">{t('factCard.draftHint')}</p>
              <Button asChild variant="outline" size="sm" className="mt-2 cursor-pointer">
                <Link href="/draft-box">{t('factCard.openDraftBox')}</Link>
              </Button>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
            <Button
              type="submit"
              className="cursor-pointer"
              loading={submitting}
              disabled={projectIds.length === 0}
            >
              <Clapperboard className="mr-1.5 h-4 w-4" />
              {t('factCard.submit')}
            </Button>
            <span className={hintClass}>{t('factCard.submitHint')}</span>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

export default FactCardForm
export { FactCardForm }
