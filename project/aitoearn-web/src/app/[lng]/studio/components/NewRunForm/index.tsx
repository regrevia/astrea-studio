/**
 * NewRunForm - 口播出片表单
 *
 * 用户填主题、一行一屏的口播稿、音色和平台，提交一条 timeline 竖屏任务：
 * 卡面、立绘、逐屏配音和口型都由渲染进程按稿子生成，不用写 JSON。
 * 提交成功后把 runId 交给父组件接入轮询，同时在表单下方显示任务状态与成片。
 */
'use client'

import type { FormEvent } from 'react'
import type { VoiceOption } from './utils'
import { Plus } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { RunBatchVariant } from '@/api/production/production.api'
import type { MaterialPack } from '@/api/production/production.types'
import { createRunBatch, createRun, getInventory } from '@/api/production/production.api'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NumberInput } from '@/components/ui/number-input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { DEFAULT_PLATFORMS, PLATFORM_LABELS, PLATFORM_OPTIONS } from '../../studioConstants'
import NarrationRunResult from './components/NarrationRunResult'
import {
  buildVerticalShortSpec,
  buildVoiceOptions,
  DEFAULT_MAX_SECONDS,
  DEFAULT_VOICE,
  describeFormError,
  estimateSeconds,
  MAX_MAX_SECONDS,
  MIN_MAX_SECONDS,
  parseScriptText,
  validateNarrationForm,
} from './utils'

interface NewRunFormProps {
  /** projectId → 项目名。面板加载完成后才有值。 */
  projects: Record<string, string>
  /** 页面当前选中的项目；为空时回落到第一个项目。 */
  projectId?: string
  /** 创建成功回调：把新任务 id 交给页面选中。 */
  onCreated: (runIds: string[]) => void
}

/** 多条母版可选的三维差异，和后端 createProductionBatch 的 variants 一一对应。 */
const VARIANT_OPTIONS: { value: RunBatchVariant, label: string }[] = [
  { value: 'mirror', label: '运镜方向' },
  { value: 'opening', label: '开场镜头' },
  { value: 'hook', label: '开头字幕' },
]

/** 表单里统一的小标签样式，避免每个字段重复写一遍提示。 */
const hintClass = 'text-[11px] text-muted-foreground'

/** 口播稿示例：三段式，用户照着改成自己的选题就行。 */
const SCRIPT_PLACEHOLDER = [
  '为什么都用 Envoy :: 镜头推近面板 :: Envoy 把服务发现、限流、灰度收进一层',
  '一条命令看全链路 :: 切到终端 :: 装上之后，一条命令就能看到每条上下游的延迟',
  '不换代码就能接 :: 回到面板 :: 旧服务不用改代码，加一行配置就进来了',
].join('\n')

export default function NewRunForm({ projects, projectId: selectedProjectId, onCreated }: NewRunFormProps) {
  const projectIds = Object.keys(projects)
  const [projectId, setProjectId] = useState('')
  const [topic, setTopic] = useState('')
  const [scriptText, setScriptText] = useState('')
  const [voice, setVoice] = useState(DEFAULT_VOICE)
  const [materialsText, setMaterialsText] = useState('')
  const [maxSeconds, setMaxSeconds] = useState<number | undefined>(DEFAULT_MAX_SECONDS)
  const [platforms, setPlatforms] = useState<string[]>(DEFAULT_PLATFORMS)
  /** 1 条=单条出片；2~3 条=同稿多母版，挑一条发。 */
  const [count, setCount] = useState('1')
  /** 多条时哪几维逐条错开；默认全选，取消勾选就固定该维。 */
  const [variants, setVariants] = useState<RunBatchVariant[]>(['mirror', 'opening', 'hook'])
  const [voices, setVoices] = useState<VoiceOption[]>(() => buildVoiceOptions())
  const [packs, setPacks] = useState<MaterialPack[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState('')
  const [runId, setRunId] = useState('')

  // 音色清单来自本机能力服务：拿不到就用兜底清单，表单不因此不可用。
  useEffect(() => {
    let alive = true
    getInventory().then((inventory) => {
      if (!alive)
        return
      if (inventory?.voices)
        setVoices(buildVoiceOptions(inventory))
      if (inventory?.materials?.packs?.length)
        setPacks(inventory.materials.packs)
    })
    return () => {
      alive = false
    }
  }, [])

  // 项目列表是异步加载的：选中项为空时回落到第一个项目，避免用户被迫先点一次下拉框。
  const effectiveProjectId = projectIds.includes(projectId)
    ? projectId
    : (projectIds.includes(selectedProjectId ?? '') ? (selectedProjectId as string) : (projectIds[0] ?? ''))
  const parsed = useMemo(() => parseScriptText(scriptText), [scriptText])
  const estimated = useMemo(() => estimateSeconds(parsed.lines, voice), [parsed.lines, voice])
  const limit = maxSeconds ?? DEFAULT_MAX_SECONDS
  const overLimit = estimated > limit
  const selectedVoice = voices.find(item => item.value === voice)

  const togglePlatform = (platform: string, checked: boolean) => {
    setPlatforms(prev =>
      checked ? [...prev, platform] : prev.filter(item => item !== platform),
    )
  }

  /** 点素材包里的图：已写进文本框就撤掉，否则追加一行引用。 */
  const togglePackShot = (ref: string) => {
    setMaterialsText((prev) => {
      const lines = prev.split('\n').map(line => line.trim()).filter(Boolean)
      return lines.includes(ref)
        ? lines.filter(line => line !== ref).join('\n')
        : [...lines, ref].join('\n')
    })
  }

  const toggleVariant = (value: RunBatchVariant, checked: boolean) => {
    setVariants(current => (checked ? [...current, value] : current.filter(item => item !== value)))
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting)
      return

    const values = { topic, scriptText, voice, materialsText, maxSeconds, platforms }
    const invalid = validateNarrationForm(values, parsed.lines)
    if (invalid) {
      setFormError(describeFormError(invalid))
      return
    }
    if (!effectiveProjectId) {
      setFormError('没有可用的制作项目，先创建 project 再出片')
      return
    }

    setSubmitting(true)
    setFormError('')
    try {
      const wanted = Number(count) || 1
      const payload = {
        projectId: effectiveProjectId,
        title: topic.trim(),
        brief: topic.trim(),
        spec: { ...buildVerticalShortSpec(values, parsed.lines) },
      }
      const res = wanted > 1
        ? await createRunBatch({ ...payload, count: wanted, variants })
        : await createRun(payload)
      // 只有 code === 0 且带回 runId/runIds 才算建成功，否则留在表单里让用户改。
      const data = res?.data as { runId?: string, runIds?: string[] } | undefined
      const ids = data?.runIds?.length ? data.runIds : (data?.runId ? [data.runId] : [])
      if (res?.code !== 0 || ids.length === 0) {
        setFormError(res?.message || '提交失败，稍后重试')
        return
      }
      setRunId(ids[0])
      onCreated(ids)
    }
    finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <form className="grid gap-4 sm:grid-cols-2" onSubmit={handleSubmit}>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="new-run-project">制作项目</Label>
          <Select value={effectiveProjectId} onValueChange={setProjectId} disabled={projectIds.length === 0}>
            <SelectTrigger id="new-run-project">
              <SelectValue placeholder="选择制作项目" />
            </SelectTrigger>
            <SelectContent>
              {projectIds.map(id => (
                <SelectItem key={id} value={id}>{projects[id]}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {projectIds.length === 0 && <span className={hintClass}>还没有制作项目，先创建 project 再出片。</span>}
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="new-run-topic">主题</Label>
          <Input
            id="new-run-topic"
            value={topic}
            placeholder="例如：Envoy 为什么能把链路看清"
            onChange={event => setTopic(event.target.value)}
          />
          <span className={hintClass}>印在卡面顶栏，也作为任务标题。</span>
        </div>

        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <Label htmlFor="new-run-script">口播稿</Label>
          <Textarea
            id="new-run-script"
            rows={6}
            value={scriptText}
            placeholder={SCRIPT_PLACEHOLDER}
            onChange={event => setScriptText(event.target.value)}
          />
          <span className={hintClass}>一行一屏：标题 :: 镜头注解 :: 口播句</span>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="new-run-voice">音色</Label>
          <Select value={voice} onValueChange={setVoice}>
            <SelectTrigger id="new-run-voice">
              <SelectValue placeholder="选择音色" />
            </SelectTrigger>
            <SelectContent>
              {voices.map(item => (
                <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {selectedVoice?.note && <span className={hintClass}>{selectedVoice.note}</span>}
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="new-run-max-seconds">时长上限</Label>
          <NumberInput
            id="new-run-max-seconds"
            className="w-32"
            value={maxSeconds}
            min={MIN_MAX_SECONDS}
            max={MAX_MAX_SECONDS}
            allowNegative={false}
            decimalScale={0}
            onValueChange={setMaxSeconds}
          />
          {parsed.lines.length > 0 && (
            <span className={overLimit ? 'text-[11px] text-destructive' : hintClass}>
              预计
              {' '}
              {Math.round(estimated)}
              {' '}
              秒 / 上限
              {' '}
              {limit}
              {' '}
              秒
            </span>
          )}
        </div>

        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <Label htmlFor="new-run-materials">素材（可选）</Label>
          <Textarea
            id="new-run-materials"
            rows={2}
            value={materialsText}
            placeholder={'/Users/me/materials/a.png\npack:envoy/hero'}
            onChange={event => setMaterialsText(event.target.value)}
          />
          <span className={hintClass}>每行一个，第几条进第几屏，图插在卡面收尾前。</span>
          {packs.length > 0 && (
            <div className="flex flex-col gap-1.5">
              {packs.map(pack => (
                  <div key={pack.slug} className="flex flex-wrap items-center gap-2">
                    <span className={hintClass}>
                      {pack.slug}
                      {' · '}
                      {pack.shots}
                      {' 张'}
                    </span>
                    {pack.refs.map((ref, index) => (
                      <Button
                        key={ref}
                        type="button"
                        size="sm"
                        title={ref}
                        variant={materialsText.split('\n').map(line => line.trim()).includes(ref) ? 'default' : 'outline'}
                        onClick={() => togglePackShot(ref)}
                      >
                        第
                        {' '}
                        {index + 1}
                        {' '}
                        张
                      </Button>
                    ))}
                  </div>
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <Label>发布平台</Label>
          <div className="flex flex-wrap gap-x-5 gap-y-2">
            {PLATFORM_OPTIONS.map((platform) => {
              const checked = platforms.includes(platform)
              const id = `new-run-platform-${platform}`
              return (
                <div key={platform} className="flex items-center gap-2">
                  <Checkbox
                    id={id}
                    checked={checked}
                    onCheckedChange={next => togglePlatform(platform, next === true)}
                  />
                  <Label htmlFor={id} className="cursor-pointer text-sm font-normal">
                    {PLATFORM_LABELS[platform] ?? platform}
                  </Label>
                </div>
              )
            })}
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="new-run-count">母版条数</Label>
          <Select value={count} onValueChange={setCount}>
            <SelectTrigger id="new-run-count" className="w-32 cursor-pointer">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {['1', '2', '3'].map(n => (
                <SelectItem key={n} value={n}>{n}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {Number(count) > 1 && (
          <fieldset className="flex flex-col gap-2 sm:col-span-2">
            <legend className="text-sm font-medium">多条之间怎么不同</legend>
            <div className="flex flex-wrap gap-x-6 gap-y-2">
              {VARIANT_OPTIONS.map(option => (
                <div key={option.value} className="flex items-center gap-2">
                  <Checkbox
                    id={`run-variant-${option.value}`}
                    checked={variants.includes(option.value)}
                    onCheckedChange={next => toggleVariant(option.value, next === true)}
                  />
                  <Label htmlFor={`run-variant-${option.value}`} className="cursor-pointer text-sm font-normal">
                    {option.label}
                  </Label>
                </div>
              ))}
            </div>
            <p className={hintClass}>第 1 条始终按原稿，勾选的维度从第 2 条起逐条错开</p>
          </fieldset>
        )}

        {formError && (
          <p role="alert" className="text-sm text-destructive sm:col-span-2">{formError}</p>
        )}

        <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
          <Button type="submit" loading={submitting} className="cursor-pointer">
            {!submitting && <Plus className="h-4 w-4" />}
            出片
          </Button>
        </div>
      </form>

      {runId && <NarrationRunResult key={runId} runId={runId} />}
    </div>
  )
}
