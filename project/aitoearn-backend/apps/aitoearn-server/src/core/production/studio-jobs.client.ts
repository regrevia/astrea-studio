import { AppException, ResponseCode } from '@yikart/common'

/** 宿主机渲染队列（capabilities server）支持的出片引擎，和 integrations/mac-worker/jobs.py 的 KINDS 保持一致。 */
export enum StudioJobKind {
  PAPER = 'paper',
  SLIDES = 'slides',
  JIANYING = 'jianying',
  MOTION = 'motion',
  TIMELINE = 'timeline',
}

/** 队列任务的只读视图。字段是队列真实返回的子集，别在这里改名。 */
export interface StudioJob {
  id: string
  kind: string
  status: string
  progress: number
  stage: string
  error?: string
  result?: {
    files?: string[]
    duration?: number
    returncode?: number
  }
}

export interface StudioJobCancelResult extends StudioJob {
  cancelled: boolean
}

/**
 * 宿主机渲染队列的 HTTP 客户端。队列跑在用户自己的机器上（默认 8899），
 * 容器要经 host.docker.internal 访问，因此这里只做「下单 / 查状态 / 取消」三件事，
 * 任何网络或协议问题都收敛成一个可读异常，由工具层翻成人话回灌给模型。
 */
export class StudioJobsClient {
  constructor(private readonly baseUrl: string) {}

  /** 下单出片。队列是异步的，返回时任务通常还在排队。 */
  async submit(kind: StudioJobKind, payload: Record<string, unknown>): Promise<StudioJob> {
    const body = await this.request('POST', '/jobs', { kind, payload })
    return asJob(body)
  }

  /** 查一条任务的状态机；任务不存在返回 null，让调用方自己决定怎么说。 */
  async get(jobId: string): Promise<StudioJob | null> {
    const body = await this.request('GET', `/jobs/${encodeURIComponent(jobId)}`)
    return body === null ? null : asJob(body)
  }

  /** 取消排队中或正在跑的任务；已结束的任务队列会如实回 cancelled=false。 */
  async cancel(jobId: string): Promise<StudioJobCancelResult | null> {
    const body = await this.request('POST', `/jobs/${encodeURIComponent(jobId)}/cancel`, {})
    if (body === null)
      return null
    const job = asJob(body)
    return { ...job, cancelled: job.status === 'cancelled' }
  }

  private async request(method: 'GET' | 'POST', path: string, body?: unknown): Promise<unknown> {
    const url = `${this.baseUrl.replace(/\/+$/, '')}${path}`
    let response: Response
    try {
      response = await fetch(url, {
        method,
        headers: body === undefined ? undefined : { 'content-type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(20000),
      })
    }
    catch (error) {
      throw new AppException(
        ResponseCode.ProductionRenderQueueUnavailable,
        `渲染队列连不上：${error instanceof Error ? error.message : String(error)}`,
      )
    }
    if (response.status === 404)
      return null
    if (!response.ok) {
      const detail = await response.text().catch(() => '')
      throw new AppException(
        ResponseCode.ProductionRenderQueueUnavailable,
        `渲染队列返回 ${response.status}：${detail.slice(0, 200) || '无正文'}`,
      )
    }
    return await response.json().catch(() => ({}))
  }
}

function asJob(body: unknown): StudioJob {
  const raw = (body ?? {}) as Partial<StudioJob>
  const job: StudioJob = {
    id: String(raw.id ?? ''),
    kind: String(raw.kind ?? ''),
    status: String(raw.status ?? 'unknown'),
    progress: raw.progress ?? 0,
    stage: String(raw.stage ?? ''),
  }
  if (raw.error)
    job.error = String(raw.error)
  if (raw.result)
    job.result = raw.result
  return job
}
