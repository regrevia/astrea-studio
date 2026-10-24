import { Prop, Schema, SchemaFactory } from '@nestjs/mongoose'
import { DEFAULT_SCHEMA_OPTIONS } from '../mongodb.constants'
import { WithTimestampSchema } from './timestamp.schema'

/** 会话消息的说话方。 */
export enum ProductionAssistantRole {
  USER = 'user',
  ASSISTANT = 'assistant',
}

/** 一条消息里附带的可点击动作类型，和前端 AssistantActionType 一一对应。 */
export enum ProductionAssistantActionType {
  RESEARCH = 'research',
  MATERIAL = 'material',
  RUN = 'run',
  RENDER = 'render',
  DRAFT = 'draft',
  PUBLISH = 'publish',
  INFO = 'info',
}

export enum ProductionAssistantActionStatus {
  RUNNING = 'running',
  OK = 'ok',
  FAILED = 'failed',
}

@Schema({ _id: false })
export class ProductionAssistantAction {
  @Prop({ required: true })
  id: string

  @Prop({ required: true, enum: ProductionAssistantActionType })
  type: ProductionAssistantActionType

  @Prop({ required: true, enum: ProductionAssistantActionStatus })
  status: ProductionAssistantActionStatus

  /** 一句话说清这一步做了什么，例如「已创建任务 30 秒竖屏」。 */
  @Prop({ required: true })
  label: string

  /** 一行可读细节，例如「douyin 1080x1920 · 1.7 MB」。 */
  @Prop({ default: '' })
  detail?: string

  /** 站内路径（/studio、/draft-box）或产物地址。 */
  @Prop({ default: '' })
  link?: string
}

export const ProductionAssistantActionSchema = SchemaFactory.createForClass(ProductionAssistantAction)

@Schema({ _id: false })
export class ProductionAssistantMessage {
  @Prop({ required: true })
  id: string

  @Prop({ required: true, enum: ProductionAssistantRole })
  role: ProductionAssistantRole

  @Prop({ required: true, default: '' })
  content: string

  @Prop({ required: true, type: Date })
  createdAt: Date

  @Prop({ type: [ProductionAssistantActionSchema], default: [] })
  actions: ProductionAssistantAction[]
}

export const ProductionAssistantMessageSchema = SchemaFactory.createForClass(ProductionAssistantMessage)

/**
 * 联网调研的落库结果。给模型的正文只有压缩后的 2000 字，
 * 全文留在这里，事后能追问「刚才那篇写了什么」。
 */
@Schema({ _id: false })
export class ProductionAssistantResearch {
  @Prop({ required: true })
  id: string

  @Prop({ required: true })
  url: string

  @Prop({ default: '' })
  title: string

  @Prop({ required: true, type: Number, default: 0 })
  chars: number

  @Prop({ required: true, default: '' })
  text: string

  @Prop({ required: true, type: Date })
  createdAt: Date
}

export const ProductionAssistantResearchSchema = SchemaFactory.createForClass(ProductionAssistantResearch)

@Schema({ ...DEFAULT_SCHEMA_OPTIONS, collection: 'productionAssistantSession' })
export class ProductionAssistantSession extends WithTimestampSchema {
  id: string

  @Prop({ required: true, index: true })
  userId: string

  @Prop({ required: true, default: 'Studio 指挥台会话' })
  title: string

  @Prop({ type: [ProductionAssistantMessageSchema], default: [] })
  messages: ProductionAssistantMessage[]

  @Prop({ type: [ProductionAssistantResearchSchema], default: [] })
  research: ProductionAssistantResearch[]
}

export const ProductionAssistantSessionSchema = SchemaFactory.createForClass(ProductionAssistantSession)
// 会话列表只按用户+最近更新时间取，不做全表扫描
ProductionAssistantSessionSchema.index({ userId: 1, updatedAt: -1 })
