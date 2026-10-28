import { Injectable } from '@nestjs/common'
import { InjectModel } from '@nestjs/mongoose'
import { Model } from 'mongoose'
import {
  ProductionAssistantMessage,
  ProductionAssistantResearch,
  ProductionAssistantSession,
} from '../schemas/production-assistant-session.schema'
import { BaseRepository } from './base.repository'

@Injectable()
export class ProductionAssistantSessionRepository extends BaseRepository<ProductionAssistantSession> {
  constructor(
    @InjectModel(ProductionAssistantSession.name) model: Model<ProductionAssistantSession>,
  ) {
    super(model)
  }

  /** 会话只能被创建者读到，userId 永远参与过滤条件。 */
  async getByIdForUser(id: string, userId: string) {
    return await this.findOne({ _id: id, userId })
  }

  /** 最近会话列表：只按创建者过滤，按更新时间倒序。 */
  async listByUser(userId: string, limit: number) {
    return await this.model
      .find({ userId })
      .sort({ updatedAt: -1 })
      .limit(limit)
      .lean()
      .exec()
  }

  async appendMessage(id: string, userId: string, message: ProductionAssistantMessage) {
    return await this.updateOne({ _id: id, userId }, { $push: { messages: message } } as never)
  }

  async appendResearch(id: string, userId: string, research: ProductionAssistantResearch) {
    return await this.updateOne({ _id: id, userId }, { $push: { research } } as never)
  }
}
