/**
 * studio/utils/runArtifacts - 从 run 文档派生可交付产物
 *
 * 后端只把 worker 的 deliveries 归一化进 output（母版、平台成片、体检）， // ui-copy-ignore
 * 剪映草稿路径不进 output：草稿是「落到本机剪映草稿箱」的交付，靠 spec.engine === 'jianying'
 * 和 jianying_draft 阶段的留痕判断，不虚构字段。发布草稿是 materialId 指的 AiToEarn 草稿箱条目。
 */
import type { ProductionCopyFinding, ProductionRun } from '@/api/production/production.types'

export interface PlatformArtifact {
  platform: string
  url: string
  width?: number
  height?: number
  bytes?: number
}

export interface RunArtifacts {
  /** 母版 mp4。剪映通道没有母版，只有草稿。 */
  masterUrl?: string
  masterPath?: string
  duration?: number
  platforms: PlatformArtifact[]
  /** 这条任务走的是剪映草稿通道：产物是草稿目录，导出要用户在剪映里做。 */
  jianying: boolean
  jianyingDelivered: boolean
  /** 用户自己在剪映里导出、平台认领入库的成片：不需要再开导出面板。 */
  jianyingClaimed: boolean
  /** 成片在库里的落点：有它说明片子已按规范归档，不是临时文件。 */
  libraryPath?: string
  libraryAssetId?: string
  /** 发布草稿（AiToEarn 草稿箱）条目 id。 */
  publishDraftId?: string
  copyFindings: ProductionCopyFinding[]
  qaFindings: ProductionCopyFinding[]
}

/** 剪映草稿落盘后后端会写 jianying_draft 阶段；改稿重跑也会重新落一次。 */
function touchesJianying(run: ProductionRun): boolean {
  if (run.spec?.engine === 'jianying')
    return true
  if (run.currentStage?.includes('jianying'))
    return true
  return (run.checkpoints ?? []).some(point => point.stage?.includes('jianying'))
}

export function deriveRunArtifacts(run?: ProductionRun | null): RunArtifacts {
  const empty: RunArtifacts = {
    platforms: [],
    jianying: false,
    jianyingDelivered: false,
    jianyingClaimed: false,
    copyFindings: [],
    qaFindings: [],
  }
  if (!run)
    return empty

  const output = run.output
  const platforms = Object.entries(output?.platforms ?? {})
    .filter(([, video]) => !!video?.url)
    .map(([platform, video]) => ({
      platform,
      url: video.url, // ui-copy-ignore
      width: video.width, // ui-copy-ignore
      height: video.height, // ui-copy-ignore
      bytes: video.bytes, // ui-copy-ignore
    }))

  return {
    masterUrl: output?.videoUrl ?? run.videoUrl,
    masterPath: output?.videoPath,
    duration: output?.duration ?? run.duration,
    platforms,
    jianying: touchesJianying(run),
    // 草稿是不是真落了，只能看阶段有没有走到；没走到就别让用户去剪映里白找。
    jianyingDelivered: run.status === 'succeeded' && run.currentStage?.includes('jianying') === true,
    jianyingClaimed: output?.claimed === true,
    libraryPath: output?.libraryPath,
    libraryAssetId: output?.libraryAssetId,
    publishDraftId: run.materialId,
    copyFindings: (output?.copyCheck?.findings ?? []).filter(item => item.level === 'warn'),
    qaFindings: output?.videoQa?.findings ?? [],
  }
}
