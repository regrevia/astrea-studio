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

  /**
   * Compare-and-set on the run revision: a writer that read revision N only
   * updates the run while it is still at N (and matches `filter`, e.g. a status
   * guard). Returns null when someone else already moved the run on.
   */
  async updateByRevision(
    id: string,
    revision: number,
    update: Record<string, unknown>,
    filter: Record<string, unknown> = {},
  ) {
    return await this.updateOne({ _id: id, revision, ...filter }, update)
  }
}

@Injectable()
export class ProductionJobRepository extends BaseRepository<ProductionJob> {
  constructor(@InjectModel(ProductionJob.name) model: Model<ProductionJob>) {
    super(model)
  }

  /**
   * Atomically take the oldest queued job in a lane for this authenticated
   * worker. A worker may only claim jobs of its own user and of the kinds it
   * advertises; an empty capability list claims nothing.
   */
  async claimNext(
    lane: string,
    workerId: string,
    leaseMs: number,
    capabilities: string[],
    userId: string,
  ): Promise<ProductionJob | null> {
    if (!capabilities.length) {
      return null
    }
    const now = new Date()
    const claimed = await this.model.findOneAndUpdate(
      {
        status: ProductionJobStatus.QUEUED,
        lane,
        userId,
        kind: { $in: capabilities },
      },
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
      { sort: { createdAt: 1 }, new: true },
    ).lean({ virtuals: true }).exec()
    return claimed as unknown as ProductionJob | null
  }

  /**
   * Renew a lease and write the progress patch in the same update. Returns null
   * when the caller is stale (owner, version, state or an already expired lease)
   * so a zombie worker cannot keep writing events or beat the reaper.
   */
  async renewLease(
    id: string,
    workerId: string,
    leaseVersion: number,
    leaseMs: number,
    update: Record<string, unknown> = {},
  ) {
    const now = new Date()
    return await this.model.findOneAndUpdate(
      {
        _id: id,
        leaseOwner: workerId,
        leaseVersion,
        status: ProductionJobStatus.RUNNING,
        leaseExpiresAt: { $gt: now },
      },
      { $set: { ...update, leaseExpiresAt: new Date(now.getTime() + leaseMs), heartbeatAt: now } },
      { new: true, lean: true },
    ).exec() as unknown as ProductionJob | null
  }

  /**
   * Guarded completion: only the current lease holder, on a running job with an
   * unexpired lease, for the run revision the job was enqueued for. A late job
   * from an old revision can never finish.
   */
  async finish(
    id: string,
    workerId: string,
    leaseVersion: number,
    revision: number,
    update: Record<string, unknown>,
  ) {
    const res = await this.model.findOneAndUpdate(
      {
        _id: id,
        leaseOwner: workerId,
        leaseVersion,
        status: ProductionJobStatus.RUNNING,
        leaseExpiresAt: { $gt: new Date() },
        revision: { $in: [revision, null] },
      },
      { $set: { ...update, finishedAt: new Date(), leaseExpiresAt: null } },
      { new: true, lean: true },
    ).exec()
    return res as unknown as ProductionJob | null
  }

  /**
   * Expire a lease the reaper observed as expired. Matching the observed
   * `leaseExpiresAt` means a concurrent renewal wins: the document moved, so
   * this update matches nothing and the job is not killed.
   */
  async updateByLeaseExpiry(
    id: string,
    leaseOwner: string,
    leaseVersion: number,
    leaseExpiresAt: Date,
    update: Record<string, unknown>,
  ) {
    const res = await this.model.findOneAndUpdate(
      {
        _id: id,
        leaseOwner,
        leaseVersion,
        status: ProductionJobStatus.RUNNING,
        leaseExpiresAt,
      },
      { $set: { ...update, finishedAt: new Date(), leaseExpiresAt: null }, $inc: { leaseVersion: 1 } },
      { new: true, lean: true },
    ).exec()
    return res as unknown as ProductionJob | null
  }

  /**
   * Cancel a job only when it is still in one of the expected states. The caller
   * passes `$inc: { leaseVersion: 1 }` so a worker that already holds the lease
   * fails every later guarded write.
   */
  async updateByStatusIn(id: string, statuses: ProductionJobStatus[], update: Record<string, unknown>) {
    return await this.updateOne({ _id: id, status: { $in: statuses } }, update)
  }

  async findExpired(now: Date) {
    return await this.find({
      status: ProductionJobStatus.RUNNING,
      leaseExpiresAt: { $lt: now },
    })
  }

  async listByRun(runId: string, revision?: number) {
    const filter: Record<string, unknown> = { runId }
    if (revision !== undefined) {
      // 旧 job 没有 revision 字段，按 revision 1 处理。
      filter['revision'] = { $in: [revision, null] }
    }
    return await this.find(filter, { sort: { createdAt: 1 } })
  }

  /** Queued/running jobs of earlier revisions, which a revision bump invalidates. */
  async listByRunBeforeRevision(runId: string, revision: number) {
    return await this.find({
      runId,
      status: { $in: [ProductionJobStatus.QUEUED, ProductionJobStatus.RUNNING] },
      $or: [{ revision: { $lt: revision } }, { revision: null }],
    })
  }

  /**
   * Next attempt number for a run+kind. Deliberately ignores revision so attempt
   * numbers stay unique per run+kind: retrying revision N+1 must not collide with
   * the old revision's attempt under either the old or the new unique index.
   */
  async nextAttempt(runId: string, kind: string): Promise<number> {
    const jobs = await this.find({ runId, kind }, { sort: { attempt: -1 }, limit: 1 })
    return (jobs[0]?.attempt ?? 0) + 1
  }

  async listByStatus(lane: string, status: ProductionJobStatus, userId?: string) {
    const filter: Record<string, unknown> = { lane, status }
    if (userId !== undefined) {
      filter['userId'] = userId
    }
    return await this.find(filter)
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

  /**
   * 卡在 submitting 的记录：浏览器那条路走到一半进程没了，记录就永远停在提交中。
   * 按 updatedAt 找出来才能交给人工回读，而不是自动重投一遍。
   */
  async listStaleByStatus(status: string, olderThan: Date, options: { userId?: string, limit?: number } = {}) {
    return await this.find(
      { status, updatedAt: { $lt: olderThan }, ...(options.userId ? { userId: options.userId } : {}) } as never,
      { sort: { updatedAt: 1 }, limit: options.limit ?? 20 },
    )
  }

  /** 只有仍然处在给定状态时才允许改，避免巡检和真实回写互相覆盖。 */
  async updateStatusIn(id: string, from: string[], patch: Record<string, unknown>) {
    return await this.updateOne(
      { _id: id, status: { $in: from } } as never,
      { $set: patch } as never,
    )
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
