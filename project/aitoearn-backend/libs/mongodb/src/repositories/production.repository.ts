import { Injectable } from '@nestjs/common'
import { InjectModel } from '@nestjs/mongoose'
import { Model } from 'mongoose'
import {
  ProductionEvent,
  ProductionJob,
  ProductionJobStatus,
  ProductionProject,
  ProductionRun,
  ProductionWorker,
  PublicationAttempt,
} from '../schemas/production.schema'
import { BaseRepository } from './base.repository'

@Injectable()
export class ProductionProjectRepository extends BaseRepository<ProductionProject> {
  constructor(@InjectModel(ProductionProject.name) model: Model<ProductionProject>) {
    super(model)
  }

  async listByUser(userId: string) {
    return await this.find({ userId }, { sort: { createdAt: -1 } })
  }

  async getByIdForUser(id: string, userId: string) {
    return await this.findOne({ _id: id, userId })
  }
}

@Injectable()
export class ProductionRunRepository extends BaseRepository<ProductionRun> {
  constructor(@InjectModel(ProductionRun.name) model: Model<ProductionRun>) {
    super(model)
  }

  async getByIdForUser(id: string, userId: string) {
    return await this.findOne({ _id: id, userId })
  }

  async listByUser(userId: string, page: number, pageSize: number, filter: Record<string, unknown> = {}) {
    return await this.findWithPagination({
      page,
      pageSize,
      filter: { userId, ...filter },
      options: { sort: { createdAt: -1 } },
    })
  }
}

@Injectable()
export class ProductionJobRepository extends BaseRepository<ProductionJob> {
  constructor(@InjectModel(ProductionJob.name) model: Model<ProductionJob>) {
    super(model)
  }

  /** Atomically take the oldest queued job in a lane for this worker. */
  async claimNext(lane: string, workerId: string, leaseMs: number, capabilities: string[]): Promise<ProductionJob | null> {
    const now = new Date()
    const filter: Record<string, unknown> = {
      status: ProductionJobStatus.QUEUED,
      lane,
    }
    if (capabilities.length) {
      filter['kind'] = { $in: capabilities }
    }
    const claimed = await this.model.findOneAndUpdate(
      filter,
      {
        $set: {
          status: ProductionJobStatus.RUNNING,
          leaseOwner: workerId,
          leaseExpiresAt: new Date(now.getTime() + leaseMs),
          heartbeatAt: now,
          startedAt: now,
        },
        $inc: { leaseVersion: 1 },
      },
      { sort: { createdAt: 1 }, new: true, lean: true },
    ).exec()
    return claimed as unknown as ProductionJob | null
  }

  /**
   * Renew a lease. Returns null when the caller is stale (lease version or owner
   * mismatch) so a zombie worker cannot keep writing events.
   */
  async renewLease(id: string, workerId: string, leaseVersion: number, leaseMs: number) {
    const now = new Date()
    return await this.model.findOneAndUpdate(
      {
        _id: id,
        leaseOwner: workerId,
        leaseVersion,
        status: ProductionJobStatus.RUNNING,
      },
      { $set: { leaseExpiresAt: new Date(now.getTime() + leaseMs), heartbeatAt: now } },
      { new: true, lean: true },
    ).exec() as unknown as ProductionJob | null
  }

  /** Guarded completion: only the current lease holder may finish the job. */
  async finish(id: string, workerId: string, leaseVersion: number, update: Record<string, unknown>) {
    const res = await this.model.findOneAndUpdate(
      { _id: id, leaseOwner: workerId, leaseVersion },
      { $set: { ...update, finishedAt: new Date(), leaseExpiresAt: null } },
      { new: true, lean: true },
    ).exec()
    return res as unknown as ProductionJob | null
  }

  async findExpired(now: Date) {
    return await this.find({
      status: ProductionJobStatus.RUNNING,
      leaseExpiresAt: { $lt: now },
    })
  }

  async listByRun(runId: string) {
    return await this.find({ runId }, { sort: { createdAt: 1 } })
  }

  async nextAttempt(runId: string, kind: string): Promise<number> {
    const jobs = await this.find({ runId, kind }, { sort: { attempt: -1 }, limit: 1 })
    return (jobs[0]?.attempt ?? 0) + 1
  }

  async listByStatus(lane: string, status: ProductionJobStatus) {
    return await this.find({ lane, status })
  }

  async listQueuedCount(lane: string) {
    return await this.count({ lane, status: ProductionJobStatus.QUEUED })
  }
}

@Injectable()
export class PublicationAttemptRepository extends BaseRepository<PublicationAttempt> {
  constructor(@InjectModel(PublicationAttempt.name) model: Model<PublicationAttempt>) {
    super(model)
  }

  async getByIdempotencyKey(key: string) {
    return await this.findOne({ idempotencyKey: key })
  }

  async listByRun(runId: string) {
    return await this.find({ runId }, { sort: { createdAt: 1 } })
  }

  async listByUser(userId: string, limit = 100) {
    return await this.find({ userId }, { sort: { createdAt: -1 }, limit })
  }

  /** Count live (non-terminal) attempts sharing one real account. */
  async countActiveForAccount(accountRef: string) {
    return await this.count({
      accountRef,
      status: {
        $in: [
          'planned',
          'submitting',
          'submitted_unverified',
          'submission_unknown',
          'reconciling',
          'scheduled_remote',
          'reviewing',
        ],
      },
    })
  }
}

@Injectable()
export class ProductionWorkerRepository extends BaseRepository<ProductionWorker> {
  constructor(@InjectModel(ProductionWorker.name) model: Model<ProductionWorker>) {
    super(model)
  }

  async getByTokenHash(tokenHash: string) {
    return await this.findOne({ tokenHash, enabled: true })
  }

  async touch(id: string) {
    await this.updateById(id, { lastSeenAt: new Date() })
  }

  async listByUser(userId: string) {
    return await this.find({ userId }, { sort: { createdAt: -1 } })
  }
}

@Injectable()
export class ProductionEventRepository extends BaseRepository<ProductionEvent> {
  constructor(@InjectModel(ProductionEvent.name) model: Model<ProductionEvent>) {
    super(model)
  }

  async listByRun(runId: string, afterSeq = 0, limit = 200) {
    return await this.find({ runId, seq: { $gt: afterSeq } }, { sort: { seq: 1 }, limit })
  }

  async lastSeq(runId: string): Promise<number> {
    const rows = await this.find({ runId }, { sort: { seq: -1 }, limit: 1 })
    return rows[0]?.seq ?? 0
  }

  /** Idempotent append by (runId, seq): a duplicate write is ignored. */
  async append(data: Partial<ProductionEvent>) {
    try {
      return await this.model.create(data)
    }
    catch (error) {
      if ((error as { code?: number }).code === 11000) {
        return null
      }
      throw error
    }
  }
}
