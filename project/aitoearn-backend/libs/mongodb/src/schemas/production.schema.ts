import { Prop, Schema, SchemaFactory } from '@nestjs/mongoose'
import { DEFAULT_SCHEMA_OPTIONS } from '../mongodb.constants'
import { WithTimestampSchema } from './timestamp.schema'

/** Which component actually performs the publication. */
export enum PublicationDispatcher {
  AITOEARN = 'aitoearn',
  SAU = 'sau',
  MANUAL = 'manual',
}

export enum ProductionJobKind {
  RENDER_MPT = 'render_mpt',
  RENDER_FFMPEG = 'render_ffmpeg',
  SLIDES_RENDER = 'slides_render',
  MOTION_RENDER = 'motion_render',
  /** 自有 timeline spec 直接渲染母版（纯 ffmpeg，无 GUI） */
  TIMELINE_RENDER = 'render_timeline',
  JIANYING_DRAFT = 'jianying_draft',
  /** 从剪映工程收导出产物：出草稿成功不等于成片存在，两步分开记账 */
  JIANYING_EXPORT = 'jianying_export',
  /** 只把草稿在剪映里打开给人改，不导出 */
  JIANYING_OPEN = 'jianying_open',
  PUBLISH_SAU = 'publish_sau',
  /** 回读作品链接：只读，不发布；用来给回执不明的提交收尾 */
  PUBLISH_READBACK = 'publish_readback',
  PROBE = 'probe',
}

export enum ProductionJobStatus {
  QUEUED = 'queued',
  RUNNING = 'running',
  SUCCEEDED = 'succeeded',
  FAILED = 'failed',
  WAITING_USER = 'waiting_user',
  INTERRUPTED = 'interrupted',
  CANCELLED = 'cancelled',
}

/**
 * Publication lifecycle. Kept separate from the render job lifecycle: a render
 * can succeed while publication is unknown, and an unknown publication must
 * never be re-submitted automatically.
 */
export enum PublicationStatus {
  PLANNED = 'planned',
  SUBMITTING = 'submitting',
  SUBMITTED_UNVERIFIED = 'submitted_unverified',
  SUBMISSION_UNKNOWN = 'submission_unknown',
  RECONCILING = 'reconciling',
  SCHEDULED_REMOTE = 'scheduled_remote',
  REVIEWING = 'reviewing',
  PUBLISHED = 'published',
  REJECTED = 'rejected',
  WAITING_USER = 'waiting_user',
  NOT_SUBMITTED = 'not_submitted',
}

export enum ProductionRunStatus {
  DRAFT = 'draft',
  QUEUED = 'queued',
  RUNNING = 'running',
  SUCCEEDED = 'succeeded',
  FAILED = 'failed',
  WAITING_USER = 'waiting_user',
  CANCELLED = 'cancelled',
}

@Schema({ ...DEFAULT_SCHEMA_OPTIONS, collection: 'productionProject' })
export class ProductionProject extends WithTimestampSchema {
  id: string

  @Prop({ required: true, index: true })
  userId: string

  @Prop({ required: true })
  name: string

  /** Upstream repository / product this project promotes. */
  @Prop({ default: '' })
  sourceRepo: string

  /** Draft box used for the produced material. */
  @Prop({ default: '' })
  materialGroupId: string

  @Prop({ type: Object, default: {} })
  brand: Record<string, unknown>

  /**
   * 最近两批「一次出多版」：{ runIds, at }，新的在前。
   *
   * 工作区靠它把并排版本卡留住：刷新页面后不用再回任务列表里猜哪几条是同一批，
   * 连出两批时上一批也还能对照。只留两批，更早的批次去任务列表里翻。
   */
  @Prop({ type: [Object], default: [] })
  recentBatches: Array<{ runIds: string[], at: number }>

  /** Bound accounts: { platform, accountId, dispatcher, alias } */
  @Prop({ type: [Object], default: [] })
  accountBindings: Array<Record<string, unknown>>

  @Prop({ type: Object, default: {} })
  schedulePolicy: Record<string, unknown>

  // Defaults to false: a schema default of true made every project created via
  // the API "the default project", so `isDefault` stopped meaning anything and
  // pickers got an arbitrary project. The seeder sets true explicitly.
  @Prop({ default: false })
  isDefault: boolean
}

@Schema({ ...DEFAULT_SCHEMA_OPTIONS, collection: 'productionRun' })
export class ProductionRun extends WithTimestampSchema {
  id: string

  @Prop({ required: true, index: true })
  userId: string

  @Prop({ required: true, index: true })
  projectId: string

  @Prop({ required: true })
  title: string

  /** The user's raw request, kept verbatim for revision comparisons. */
  @Prop({ default: '' })
  brief: string

  /** Monotonic content revision; a new revision invalidates prior approval. */
  @Prop({ required: true, default: 1 })
  revision: number

  @Prop({ type: Object, default: {} })
  facts: Record<string, unknown>

  /** Structured spec handed to the renderer (script, shots, voice, subtitles). */
  @Prop({ type: Object, default: {} })
  spec: Record<string, unknown>

  @Prop({ type: Object, default: {} })
  output: Record<string, unknown>

  @Prop({ default: '' })
  materialId: string

  @Prop({ type: String, enum: ProductionRunStatus, default: ProductionRunStatus.DRAFT, index: true })
  status: ProductionRunStatus

  @Prop({ default: '' })
  currentStage: string

  /** 0-100，跟 job 的心跳同步：列表和指挥台都不该只看到「排队中」。 */
  @Prop({ default: 0 })
  progress: number

  @Prop({ default: '' })
  lastError: string

  @Prop({ type: [Object], default: [] })
  checkpoints: Array<Record<string, unknown>>
}

@Schema({ ...DEFAULT_SCHEMA_OPTIONS, collection: 'productionJob' })
export class ProductionJob extends WithTimestampSchema {
  id: string

  @Prop({ required: true, index: true })
  userId: string

  @Prop({ required: true, index: true })
  runId: string

  @Prop({ type: String, enum: ProductionJobKind, required: true })
  kind: ProductionJobKind

  @Prop({ required: true, default: 1 })
  attempt: number

  /**
   * run.revision this job was enqueued for. A job may only write back to a run
   * that is still on the same revision; legacy jobs (created before this field)
   * have no value and are treated as revision 1.
   */
  @Prop({ required: true, default: 1 })
  revision: number

  @Prop({ type: String, enum: ProductionJobStatus, default: ProductionJobStatus.QUEUED, index: true })
  status: ProductionJobStatus

  @Prop({ type: Object, default: {} })
  payload: Record<string, unknown>

  /** Render reuses a single job slot; publish only ever runs one browser. */
  @Prop({ default: 'render' })
  lane: string

  @Prop({ default: '' })
  leaseOwner: string

  @Prop({ default: 0 })
  leaseVersion: number

  @Prop({ type: Date, default: null })
  leaseExpiresAt: Date | null

  @Prop({ type: Date, default: null })
  heartbeatAt: Date | null

  @Prop({ default: '' })
  externalTaskId: string

  @Prop({ default: 0 })
  progress: number

  @Prop({ default: '' })
  stage: string

  @Prop({ default: '' })
  message: string

  @Prop({ type: Object, default: {} })
  result: Record<string, unknown>

  @Prop({ default: '' })
  lastError: string

  @Prop({ type: Date, default: null })
  startedAt: Date | null

  @Prop({ type: Date, default: null })
  finishedAt: Date | null
}

@Schema({ ...DEFAULT_SCHEMA_OPTIONS, collection: 'publicationAttempt' })
export class PublicationAttempt extends WithTimestampSchema {
  id: string

  @Prop({ required: true, index: true })
  userId: string

  @Prop({ required: true, index: true })
  runId: string

  @Prop({ required: true })
  projectId: string

  @Prop({ required: true })
  platform: string

  /** dispatcher + real platform account: the quota/lock scope. */
  @Prop({ type: String, enum: PublicationDispatcher, required: true })
  dispatcher: PublicationDispatcher

  @Prop({ required: true })
  accountRef: string

  @Prop({ default: '' })
  accountName: string

  @Prop({ required: true, default: 1 })
  revision: number

  /**
   * Idempotency key: runId + revision + platform + accountRef + slot.
   * Unique so a duplicate submission attempt is rejected at the DB level.
   */
  @Prop({ required: true })
  idempotencyKey: string

  @Prop({ type: Date, default: null })
  plannedAt: Date | null

  @Prop({ type: String, enum: PublicationStatus, default: PublicationStatus.PLANNED, index: true })
  status: PublicationStatus

  @Prop({ type: Object, default: {} })
  content: Record<string, unknown>

  @Prop({ default: '' })
  remotePostId: string

  @Prop({ default: '' })
  remoteUrl: string

  @Prop({ type: [Object], default: [] })
  evidence: Array<Record<string, unknown>>

  @Prop({ default: '' })
  lastError: string
}

@Schema({ ...DEFAULT_SCHEMA_OPTIONS, collection: 'productionWorker' })
export class ProductionWorker extends WithTimestampSchema {
  id: string

  @Prop({ required: true, index: true })
  userId: string

  @Prop({ required: true })
  name: string

  @Prop({ required: true, unique: true, index: true })
  tokenHash: string

  @Prop({ type: [String], default: [] })
  capabilities: string[]

  @Prop({ type: Date, default: null })
  lastSeenAt: Date | null

  @Prop({ default: '' })
  version: string

  @Prop({ default: true })
  enabled: boolean
}

@Schema({ ...DEFAULT_SCHEMA_OPTIONS, collection: 'productionEvent' })
export class ProductionEvent extends WithTimestampSchema {
  id: string

  @Prop({ required: true, index: true })
  runId: string

  @Prop({ default: '' })
  jobId: string

  @Prop({ required: true })
  seq: number

  @Prop({ required: true })
  kind: string

  @Prop({ default: '' })
  stage: string

  @Prop({ default: 0 })
  progress: number

  @Prop({ default: '' })
  message: string

  @Prop({ type: Object, default: {} })
  data: Record<string, unknown>
}

export const ProductionProjectSchema = SchemaFactory.createForClass(ProductionProject)
export const ProductionRunSchema = SchemaFactory.createForClass(ProductionRun)
export const ProductionJobSchema = SchemaFactory.createForClass(ProductionJob)
export const PublicationAttemptSchema = SchemaFactory.createForClass(PublicationAttempt)
export const ProductionWorkerSchema = SchemaFactory.createForClass(ProductionWorker)
export const ProductionEventSchema = SchemaFactory.createForClass(ProductionEvent)

// A (run, kind, revision, attempt) job must not be enqueued twice: a retry of a
// revised run reuses the same runId+kind, so attempt alone is not enough once a
// new revision exists. Attempt numbers stay globally unique per run+kind (see
// nextAttempt) so deployments that still carry the old
// `runId_1_kind_1_attempt_1` unique index keep working until it is dropped.
ProductionJobSchema.index({ runId: 1, kind: 1, revision: 1, attempt: 1 }, { unique: true })
// Same publication intent cannot exist twice.
PublicationAttemptSchema.index({ idempotencyKey: 1 }, { unique: true })
// Claim query: oldest queued job in a lane.
ProductionJobSchema.index({ status: 1, lane: 1, createdAt: 1 })
ProductionEventSchema.index({ runId: 1, seq: 1 }, { unique: true })
