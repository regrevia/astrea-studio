/**
 * NewRunForm - 新建时间轴任务表单
 *
 * 目标：用户不写 JSON 也能建一条 render_timeline 任务。表单只收集「友好输入」，
 * 提交时由 utils 组装成 spec，后端 timeline-spec builder 再展开成渲染器规格。
 * 提交成功后把新 runId 交给父组件接入轮询，用户马上能在「进行中」里看到它。
 */
'use client'

import type { FormEvent } from 'react'
import type { TimelineCanvas } from '@/api/production/production.types'
import { Plus } from 'lucide-react'
import { useState } from 'react'
import { createRun } from '@/api/production/production.api'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NumberInput } from '@/components/ui/number-input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { CANVAS_OPTIONS, DEFAULT_PLATFORMS, PLATFORM_LABELS, PLATFORM_OPTIONS } from '../../studioConstants'
import { buildTimelineSpec, DEFAULT_ASSET_BASE, DEFAULT_BGM_VOLUME, validateRunForm } from './utils'

interface NewRunFormProps {
  /** projectId → 项目名。面板加载完成后才有值。 */
  projects: Record<string, string>
  /** 创建成功回调：把新 runId 交给面板接入轮询。 */
  onCreated: (runId: string) => void
}

/** 表单里统一的小标签样式，避免每个字段重复写一遍提示。 */
const hintClass = 'text-[11px] text-muted-foreground'

export default function NewRunForm({ projects, onCreated }: NewRunFormProps) {
  const projectIds = Object.keys(projects)
  const [projectId, setProjectId] = useState('')
  const [title, setTitle] = useState('')
  const [scriptText, setScriptText] = useState('')
  const [materialsText, setMaterialsText] = useState('')
  const [assetBase, setAssetBase] = useState(DEFAULT_ASSET_BASE)
  const [canvas, setCanvas] = useState<TimelineCanvas>('portrait')
  const [platforms, setPlatforms] = useState<string[]>(DEFAULT_PLATFORMS)
  const [bgmSrc, setBgmSrc] = useState('')
  const [bgmVolume, setBgmVolume] = useState<number | undefined>(DEFAULT_BGM_VOLUME)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState('')
  const [successMessage, setSuccessMessage] = useState('')

  // 项目列表是异步加载的：选中项为空时回落到第一个项目，避免用户被迫先点一次下拉框。
  const effectiveProjectId = projectIds.includes(projectId) ? projectId : (projectIds[0] ?? '')

  const togglePlatform = (platform: string, checked: boolean) => {
    setPlatforms(prev =>
      checked ? [...prev, platform] : prev.filter(item => item !== platform),
    )
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting)
      return

    const values = { title, scriptText, materialsText, assetBase, canvas, platforms, bgmSrc, bgmVolume }
    const invalid = validateRunForm(values)
    if (invalid) {
      setFormError(invalid)
      setSuccessMessage('')
      return
    }
    if (!effectiveProjectId) {
      setFormError('没有可用的制作项目，先创建 project 再新建任务')
      setSuccessMessage('')
      return
    }

    setSubmitting(true)
    setFormError('')
    setSuccessMessage('')
    try {
      const res = await createRun({
        projectId: effectiveProjectId,
        title: title.trim(),
        // 展开成 Record 是给 createRun 的 spec 参数类型；字段形状由 TimelineSpecInput 约束。
        spec: { ...buildTimelineSpec(values) },
      })
      // createRun 只有 code === 0 且带回 runId 才算建成功，否则留在表单里让用户改。
      if (res?.code !== 0 || !res?.data?.runId) {
        setFormError(res?.message || '创建任务失败，请稍后重试')
        return
      }

      onCreated(res.data.runId)
      // 保留素材根目录、素材与平台选择，方便连续出片；只清掉一次性输入。
      setTitle('')
      setScriptText('')
      setBgmSrc('')
      setSuccessMessage(`已创建任务 ${res.data.runId}，worker 领取后开始渲染。`)
    }
    finally {
      setSubmitting(false)
    }
  }

  return (
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
        {projectIds.length === 0 && <span className={hintClass}>还没有制作项目，先创建 project 再建任务。</span>}
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="new-run-title">任务标题</Label>
        <Input
          id="new-run-title"
          value={title}
          placeholder="例如：三句文案 · 竖屏 · 抖音+B站"
          onChange={event => setTitle(event.target.value)}
        />
      </div>

      <div className="flex flex-col gap-1.5 sm:col-span-2">
        <Label htmlFor="new-run-asset-base">素材根目录</Label>
        <Input
          id="new-run-asset-base"
          value={assetBase}
          placeholder={DEFAULT_ASSET_BASE}
          onChange={event => setAssetBase(event.target.value)}
        />
        <span className={hintClass}>
          worker 本机可读的绝对路径；下面填的文件名都相对这个目录。
        </span>
      </div>

      <div className="flex flex-col gap-1.5 sm:col-span-2">
        <Label htmlFor="new-run-script">文案</Label>
        <Textarea
          id="new-run-script"
          rows={4}
          value={scriptText}
          placeholder={'第一句文案\n第二句文案\n第三句文案'}
          onChange={event => setScriptText(event.target.value)}
        />
        <span className={hintClass}>每行一句，按行分句；字幕与切片时长由后端按句生成。</span>
      </div>

      <div className="flex flex-col gap-1.5 sm:col-span-2">
        <Label htmlFor="new-run-materials">素材</Label>
        <Textarea
          id="new-run-materials"
          rows={3}
          value={materialsText}
          placeholder={'a.mp4\nb.mp4'}
          onChange={event => setMaterialsText(event.target.value)}
        />
        <span className={hintClass}>每行一个文件名，相对素材根目录，例如 a.mp4。</span>
      </div>

      <div className="flex flex-col gap-1.5">
        <Label>画幅</Label>
        <div className="flex flex-wrap gap-2">
          {CANVAS_OPTIONS.map((option) => {
            const selected = canvas === option.value
            return (
              <Button
                key={option.value}
                type="button"
                size="sm"
                variant={selected ? 'default' : 'outline'}
                onClick={() => setCanvas(option.value)}
              >
                {option.label}
                <span className="ml-0.5 text-[11px] opacity-70">{option.hint}</span>
              </Button>
            )
          })}
        </div>
        <span className={hintClass}>母版尺寸；各平台成片由后端按平台重新交付。</span>
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="new-run-bgm">BGM（可选）</Label>
        <div className="flex items-center gap-2">
          <Input
            id="new-run-bgm"
            className="flex-1"
            value={bgmSrc}
            placeholder="bgm.m4a"
            onChange={event => setBgmSrc(event.target.value)}
          />
          <NumberInput
            className="w-24"
            aria-label="BGM 音量"
            value={bgmVolume}
            max={1}
            decimalScale={2}
            allowNegative={false}
            onValueChange={setBgmVolume}
          />
        </div>
        <span className={hintClass}>文件名相对素材根目录；音量 0–1，留空用默认音量。</span>
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
                <code className="text-[11px] text-muted-foreground">{platform}</code>
              </div>
            )
          })}
        </div>
        <span className={hintClass}>选中几个平台就交付几份成片，母版仍会保留。</span>
      </div>

      {formError && (
        <p role="alert" className="text-sm text-destructive sm:col-span-2">{formError}</p>
      )}
      {successMessage && (
        <p role="status" className="text-sm text-primary sm:col-span-2">{successMessage}</p>
      )}

      <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
        <Button type="submit" loading={submitting}>
          {!submitting && <Plus className="h-4 w-4" />}
          创建任务
        </Button>
        <span className={hintClass}>提交后几秒内被 worker 领取，成片按平台分别交付。</span>
      </div>
    </form>
  )
}
